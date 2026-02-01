import MetaTrader5 as mt5
import time
import logging
import subprocess
import os
from datetime import datetime, timedelta
from src.data_collector import DataCollector
from src.strategy_manager import StrategyManager
from src.trade_executor import TradeExecutor
from src.utils import load_config, setup_logging, safe_log

def launch_mt5_terminal(terminal_path):
    """Launch MT5 terminal if not already running"""
    try:
        if not os.path.exists(terminal_path):
            logging.error(f"MT5 terminal not found at: {terminal_path}")
            return False
        
        logging.info("Launching MT5 terminal...")
        subprocess.Popen([terminal_path])
        time.sleep(10)  # Wait for terminal to fully load
        logging.info("MT5 terminal launched successfully")
        return True
        
    except Exception as e:
        logging.error(f"Error launching MT5 terminal: {str(e)}")
        return False

def initialize_mt5(config):
    """Initialize MetaTrader 5 connection"""
    # Try to initialize MT5
    if not mt5.initialize():
        logging.warning("MT5 not running, attempting to launch...")
        terminal_path = config['broker'].get('terminal_path')
        
        if terminal_path:
            if not launch_mt5_terminal(terminal_path):
                logging.error("Failed to launch MT5 terminal")
                return False
            
            # Try to initialize again after launching
            time.sleep(5)
            if not mt5.initialize():
                logging.error(f"MT5 initialization failed: {mt5.last_error()}")
                return False
        else:
            logging.error("MT5 terminal path not configured")
            return False
    
    # Login to trading account
    account = config['broker']['account']
    password = config['broker']['password']
    server = config['broker']['server']
    
    authorized = mt5.login(account, password=password, server=server)
    if not authorized:
        logging.error(f"Login failed: {mt5.last_error()}")
        mt5.shutdown()
        return False
    
    # Get account info
    account_info = mt5.account_info()
    if account_info:
        logging.info("="*60)
        logging.info(f"Connected to MT5 account: {account}")
        logging.info(f"Balance: ${account_info.balance:.2f}")
        logging.info(f"Equity: ${account_info.equity:.2f}")
        logging.info(f"Leverage: 1:{account_info.leverage}")
        logging.info("="*60)
    
    return True

def collect_minute_data(data_collector, symbols, duration=60):
    """Collect tick data for specified duration (in seconds)"""
    logging.info(f"Starting 1-minute data collection for {len(symbols)} symbols...")
    
    start_time = time.time()
    tick_count = {symbol: 0 for symbol in symbols}
    
    while (time.time() - start_time) < duration:
        for symbol in symbols:
            try:
                tick_data = data_collector.get_tick_data(symbol)
                if tick_data:
                    data_collector.save_tick_data(symbol, tick_data)
                    tick_count[symbol] += 1
                    
            except Exception as e:
                logging.error(f"Error collecting data for {symbol}: {str(e)}")
        
        time.sleep(0.5)  # Collect ticks every 0.5 seconds
    
    # Summary
    logging.info("="*60)
    logging.info("1-MINUTE DATA COLLECTION COMPLETED")
    logging.info("="*60)
    for symbol, count in tick_count.items():
        logging.info(f"{symbol}: {count} ticks collected")
    logging.info("="*60)

def find_strategy_file():
    """Find strategy JSON file in config directory"""
    strategy_dir = "config/strategies"
    
    # Check if strategies directory exists
    if not os.path.exists(strategy_dir):
        os.makedirs(strategy_dir, exist_ok=True)
        logging.info(f"Created strategies directory: {strategy_dir}")
        return None
    
    # Look for JSON files
    json_files = [f for f in os.listdir(strategy_dir) if f.endswith('.json')]
    
    if not json_files:
        logging.warning("No strategy JSON files found in config/strategies/")
        return None
    
    # Use the first JSON file found
    strategy_file = os.path.join(strategy_dir, json_files[0])
    logging.info(f"Found strategy file: {strategy_file}")
    
    # If multiple files, list them
    if len(json_files) > 1:
        logging.info(f"Multiple strategy files available: {json_files}")
        logging.info(f"Using: {json_files[0]}")
    
    return strategy_file

def main():
    # Setup logging
    setup_logging()
    logging.info("="*60)
    logging.info("MT5 AUTO TRADING SYSTEM STARTED")
    logging.info("="*60)
    
    # Load configuration
    broker_config = load_config('config/broker.yaml')
    trading_config = load_config('config/trading_config.yaml')
    
    # Initialize MT5
    if not initialize_mt5(broker_config):
        logging.error("Failed to initialize MT5. Exiting...")
        return
    
    # Initialize components
    data_collector = DataCollector(trading_config)
    
    # Load strategy from JSON file
    strategy_file = find_strategy_file()
    strategy_manager = StrategyManager(strategy_file)
    
    # Display strategy info
    strategy_info = strategy_manager.get_strategy_info()
    logging.info("="*60)
    logging.info("LOADED STRATEGY INFO:")
    logging.info(f"Name: {strategy_info['name']}")
    logging.info(f"Philosophy: {strategy_info.get('philosophy', 'N/A')}")
    logging.info(f"Timeframes: {strategy_info.get('timeframes', [])}")
    logging.info(f"Target Pairs: {strategy_info.get('pairs', [])}")
    logging.info("="*60)
    
    trade_executor = TradeExecutor(broker_config, trading_config, strategy_manager)
    
    # Get trading symbols from config
    symbols = trading_config['symbols']
    logging.info(f"Trading symbols: {symbols}")
    
    # Verify symbols are available
    for symbol in symbols:
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            logging.error(f"Symbol {symbol} not found!")
            continue
        if not symbol_info.visible:
            if mt5.symbol_select(symbol, True):
                logging.info(f"Symbol {symbol} enabled")
            else:
                logging.error(f"Failed to enable symbol {symbol}")
    
    try:
        logging.info("Starting main trading loop...")
        cycle_count = 0
        
        while True:
            cycle_count += 1
            current_time = datetime.now()
            
            logging.info("="*60)
            logging.info(f"CYCLE #{cycle_count} - {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
            logging.info(f"Strategy: {strategy_manager.strategy_name}")
            logging.info("="*60)
            
            # Step 1: Collect 1-minute tick data
            collect_minute_data(data_collector, symbols, duration=60)
            
            # Step 2: Process each symbol
            for symbol in symbols:
                try:
                    logging.info(f"\nProcessing {symbol}...")
                    
                    # Get OHLC data
                    ohlc_data = data_collector.get_ohlc_data(symbol, bars=100)
                    if ohlc_data is None:
                        logging.warning(f"No OHLC data for {symbol}, skipping...")
                        continue
                    
                    # Get minute data from database
                    minute_data = data_collector.get_minute_data_from_db(symbol, minutes=1)
                    if not minute_data.empty:
                        avg_bid = minute_data['bid'].mean()
                        avg_ask = minute_data['ask'].mean()
                        avg_spread = minute_data['spread'].mean()
                        
                        logging.info(f"  1-min Average - Bid: {avg_bid:.5f}, Ask: {avg_ask:.5f}, Spread: {avg_spread:.5f}")
                    
                    # Analyze with strategy
                    signal = strategy_manager.analyze(symbol, ohlc_data)
                    
                    logging.info(f"  Signal: {signal['action']} (Confidence: {signal['confidence']}%)")
                    
                    # Log key indicators
                    if signal['indicators']:
                        logging.info(f"  Indicators:")
                        for key, value in signal['indicators'].items():
                            logging.info(f"    {key}: {value:.5f}")
                    
                    # Execute trades based on signal
                    if signal['action'] != 'HOLD':
                        logging.info(safe_log(f"  ⚡ Executing {signal['action']} order..."))
                        success = trade_executor.execute_signal(symbol, signal)
                        if success:
                            logging.info(safe_log(f"  ✓ Order executed successfully"))
                        else:
                            logging.warning(safe_log(f"  ✗ Order execution failed"))
                    else:
                        logging.info(safe_log(f"  ⏸️  No action taken (HOLD)"))
                    
                except Exception as e:
                    logging.error(f"Error processing {symbol}: {str(e)}")
                    import traceback
                    logging.error(traceback.format_exc())
                    continue
            
            # Step 3: Manage existing positions
            logging.info("\nManaging open positions...")
            trade_executor.manage_positions()
            
            logging.info(f"\nCycle #{cycle_count} completed. Waiting for next cycle...")
            
    except KeyboardInterrupt:
        logging.info("\n" + "="*60)
        logging.info("Trading system stopped by user")
        logging.info("="*60)
    
    except Exception as e:
        logging.error(f"Critical error in main loop: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
    
    finally:
        # Cleanup
        mt5.shutdown()
        logging.info("MT5 connection closed")
        logging.info("System shutdown complete")

if __name__ == "__main__":
    main()