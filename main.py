import MetaTrader5 as mt5
import time
import logging
import subprocess
import os
import json
from datetime import datetime, timedelta
from src.data_collector import DataCollector
from src.strategy_manager import StrategyManager
from src.trade_executor import TradeExecutor
from src.news_collector import NewsCollector, start_news_updater
from src.utils import load_config, setup_logging, safe_log

# ======================================================================
# FIX: Global variables untuk strategy reloading
# ======================================================================
current_strategy_manager = None
strategy_file_path = None
current_trading_symbols = []  # ← FIX: Simbol trading HARUS dari strategy

# ======================================================================
# FIX: State file untuk menyimpan strategy yang sedang aktif
# ======================================================================
STATE_FILE = 'config/.current_strategy_state.json'

def save_strategy_state(strategy_name, strategy_path, strategy_info):
    """Save current strategy state to file"""
    try:
        os.makedirs('config', exist_ok=True)
        
        state = {
            'strategy_name': strategy_name,
            'strategy_path': strategy_path,
            'strategy_info': strategy_info,
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'timestamp': datetime.now().isoformat()
        }
        
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        
        logging.info(f"✓ Strategy state saved: {strategy_name}")
        
    except Exception as e:
        logging.error(f"Error saving strategy state: {str(e)}")

def load_strategy_state():
    """Load strategy state from file"""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
            return state
        return None
    except Exception as e:
        logging.error(f"Error loading strategy state: {str(e)}")
        return None

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
    if not symbols:
        logging.warning("No symbols to collect data for")
        return
    
    logging.info(f"Starting  data collection for {len(symbols)} symbols...")
    
    start_time = time.time()
    tick_count = {symbol: 0 for symbol in symbols}
    
    while (time.time() - start_time) < duration:
        # ======================================================================
        # FIX: Cek reload signal SELAMA data collection berlangsung
        # Ini agar strategy langsung reload tanpa tunggu siklus penuh (60+ detik)
        # ======================================================================
        if check_strategy_reload_signal():
            logging.info("⚡ Reload signal detected MID-COLLECTION — applying immediately!")
            reload_strategy()
            # Hentikan collection cycle ini agar loop utama bisa pakai strategy baru
            logging.info("🔄 Stopping current collection cycle to apply new strategy.")
            break

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
    
    # ======================================================================
    # FIX: Use the MOST RECENTLY MODIFIED file (newest upload)
    # ======================================================================
    json_file_paths = [os.path.join(strategy_dir, f) for f in json_files]
    latest_file = max(json_file_paths, key=os.path.getmtime)
    
    logging.info(f"✓ Found strategy file: {latest_file}")
    
    # If multiple files, list them with timestamps
    if len(json_files) > 1:
        logging.info(f"Available strategy files ({len(json_files)}):")
        for filepath in sorted(json_file_paths, key=os.path.getmtime, reverse=True):
            filename = os.path.basename(filepath)
            mod_time = datetime.fromtimestamp(os.path.getmtime(filepath))
            logging.info(f"  - {filename} (modified: {mod_time.strftime('%Y-%m-%d %H:%M:%S')})")
        logging.info(f"Using NEWEST: {os.path.basename(latest_file)}")
    
    return latest_file

def check_strategy_reload_signal():
    """Check if strategy should be reloaded"""
    signal_file = 'config/.reload_strategy'
    
    if os.path.exists(signal_file):
        try:
            # Read the signal timestamp
            with open(signal_file, 'r') as f:
                signal_time = f.read().strip()
            
            logging.info("="*60)
            logging.info("⚡ STRATEGY RELOAD SIGNAL DETECTED")
            logging.info(f"Signal time: {signal_time}")
            logging.info("="*60)
            
            # Delete the signal file AFTER reading
            try:
                os.remove(signal_file)
                logging.info("✓ Signal file removed")
            except:
                pass
            
            return True
            
        except Exception as e:
            logging.error(f"Error processing reload signal: {str(e)}")
            # Try to remove signal file anyway
            try:
                os.remove(signal_file)
            except:
                pass
            return False
    
    return False

def get_symbols_from_strategy(strategy_manager):
    """Extract symbols/pairs from strategy - MAIN FUNCTION"""
    try:
        strategy_info = strategy_manager.get_strategy_info()
        strategy_config = strategy_manager.strategy_config
        
        symbols = []
        
        # Method 1: From strategy_info['pairs']
        if 'pairs' in strategy_info and strategy_info['pairs']:
            symbols = strategy_info['pairs']
            logging.info(f"✓ Got {len(symbols)} symbols from strategy_info['pairs']: {symbols}")
            return symbols
        
        # Method 2: From strategy config directly
        if strategy_config:
            # Check in parameters > trading_pairs
            if 'parameters' in strategy_config and 'trading_pairs' in strategy_config['parameters']:
                symbols = strategy_config['parameters']['trading_pairs']
                if symbols:
                    logging.info(f"✓ Got {len(symbols)} symbols from config['parameters']['trading_pairs']: {symbols}")
                    return symbols
            
            # Check in parameters > symbols
            if 'parameters' in strategy_config and 'symbols' in strategy_config['parameters']:
                symbols = strategy_config['parameters']['symbols']
                if symbols:
                    logging.info(f"✓ Got {len(symbols)} symbols from config['parameters']['symbols']: {symbols}")
                    return symbols
            
            # Check at root level > symbols
            if 'symbols' in strategy_config:
                symbols = strategy_config['symbols']
                if symbols:
                    logging.info(f"✓ Got {len(symbols)} symbols from config['symbols']: {symbols}")
                    return symbols
            
            # Check at root level > pairs
            if 'pairs' in strategy_config:
                symbols = strategy_config['pairs']
                if symbols:
                    logging.info(f"✓ Got {len(symbols)} symbols from config['pairs']: {symbols}")
                    return symbols
        
        # Method 3: Try to parse from various strategy sections
        # Check performance targets or other sections
        if strategy_config:
            for key in ['trading_pairs', 'symbols', 'pairs', 'instruments', 'markets']:
                if key in strategy_config:
                    value = strategy_config[key]
                    if isinstance(value, list) and value:
                        symbols = value
                        logging.info(f"✓ Got {len(symbols)} symbols from config['{key}']: {symbols}")
                        return symbols
        
        # Method 4: Default jika tidak ditemukan
        if not symbols:
            symbols = ["EURUSD.s", "GBPUSD.s", "USDJPY.s"]
            logging.warning(f"⚠ No symbols found in strategy, using defaults: {symbols}")
        
        return symbols
        
    except Exception as e:
        logging.error(f"Error extracting symbols from strategy: {str(e)}")
        return ["EURUSD.s", "GBPUSD.s", "USDJPY.s"]  # Fallback default

def reload_strategy():
    """Reload the strategy from file"""
    global current_strategy_manager, strategy_file_path, current_trading_symbols
    
    try:
        logging.info("="*60)
        logging.info("🔄 RELOADING STRATEGY")
        logging.info("="*60)
        
        # Find the latest strategy file
        new_strategy_file = find_strategy_file()
        
        if new_strategy_file is None:
            logging.error("❌ No strategy file found for reload")
            return False
        
        # Log what's happening
        old_strategy_name = current_strategy_manager.strategy_name if current_strategy_manager else "None"
        old_file = os.path.basename(strategy_file_path) if strategy_file_path else "None"
        new_file = os.path.basename(new_strategy_file)
        
        if new_strategy_file == strategy_file_path:
            logging.info(f"📄 Reloading same file: {new_file}")
        else:
            logging.info(f"📄 New strategy file detected!")
            logging.info(f"   Old file: {old_file}")
            logging.info(f"   New file: {new_file}")
        
        # Create new strategy manager
        logging.info("Loading strategy configuration...")
        new_strategy_manager = StrategyManager(new_strategy_file)
        
        # ======================================================================
        # FIX: DAPATKAN SYMBOLS DARI STRATEGY FILE
        # ======================================================================
        symbols_from_strategy = get_symbols_from_strategy(new_strategy_manager)
        current_trading_symbols = symbols_from_strategy
        
        # Display new strategy info
        strategy_info = new_strategy_manager.get_strategy_info()
        logging.info("="*60)
        logging.info("✅ NEW STRATEGY LOADED:")
        logging.info(f"   Name:       {strategy_info['name']}")
        
        philosophy = strategy_info.get('philosophy', 'N/A')
        if len(philosophy) > 70:
            philosophy = philosophy[:67] + "..."
        logging.info(f"   Philosophy: {philosophy}")
        
        timeframes = strategy_info.get('timeframes', [])
        logging.info(f"   Timeframes: {', '.join(timeframes) if timeframes else 'Not specified'}")
        
        # Show trading symbols
        if symbols_from_strategy:
            if len(symbols_from_strategy) <= 5:
                logging.info(f"   Trading Pairs: {', '.join(symbols_from_strategy)}")
            else:
                logging.info(f"   Trading Pairs: {', '.join(symbols_from_strategy[:5])} (+{len(symbols_from_strategy)-5} more)")
        else:
            logging.info(f"   Trading Pairs: Not specified")
        
        logging.info("="*60)
        
        # Show strategy change
        new_strategy_name = strategy_info['name']
        if old_strategy_name != new_strategy_name:
            logging.info(f"🔄 Strategy changed: '{old_strategy_name}' → '{new_strategy_name}'")
        else:
            logging.info(f"♻️  Reloaded strategy: '{new_strategy_name}'")
        
        # Update global references
        current_strategy_manager = new_strategy_manager
        strategy_file_path = new_strategy_file
        
        # Save strategy state untuk dashboard
        save_strategy_state(
            strategy_name=strategy_info['name'],
            strategy_path=new_strategy_file,
            strategy_info={
                'name': strategy_info['name'],
                'philosophy': strategy_info.get('philosophy', 'N/A'),
                'timeframes': strategy_info.get('timeframes', []),
                'pairs': symbols_from_strategy,  # ← FIX: Simpan symbols ke state
                'performance_targets': strategy_info.get('performance_targets', {})
            }
        )
        
        logging.info("✅ Strategy reload complete!")
        logging.info("="*60)
        return True
        
    except Exception as e:
        logging.error(f"❌ Error reloading strategy: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
        return False

def verify_symbols_availability(symbols):
    """Verify that all symbols are available in MT5"""
    if not symbols:
        logging.warning("No symbols to verify")
        return []
    
    available_symbols = []
    unavailable_symbols = []
    
    for symbol in symbols:
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            # Try alternative format
            if symbol.endswith('.s'):
                alt_symbol = symbol[:-2]
            else:
                alt_symbol = f"{symbol}.s"
            
            alt_info = mt5.symbol_info(alt_symbol)
            if alt_info:
                symbol = alt_symbol
                symbol_info = alt_info
                logging.info(f"  ✓ Symbol format adjusted: {alt_symbol}")
        
        if symbol_info is None:
            unavailable_symbols.append(symbol)
            logging.error(f"  ✗ Symbol {symbol} not found in MT5!")
            continue
        
        # Enable symbol if not visible
        if not symbol_info.visible:
            if mt5.symbol_select(symbol, True):
                logging.info(f"  ✓ Enabled symbol: {symbol}")
            else:
                logging.error(f"  ✗ Failed to enable symbol: {symbol}")
                unavailable_symbols.append(symbol)
                continue
        
        available_symbols.append(symbol)
        logging.info(f"  ✓ Symbol available: {symbol}")
    
    # Summary
    logging.info("="*60)
    logging.info(f"SYMBOL VERIFICATION SUMMARY:")
    logging.info(f"Total symbols in strategy: {len(symbols)}")
    logging.info(f"Available symbols: {len(available_symbols)}")
    logging.info(f"Unavailable symbols: {len(unavailable_symbols)}")
    
    if unavailable_symbols:
        logging.warning(f"Unavailable symbols: {unavailable_symbols}")
    
    if not available_symbols:
        logging.error("❌ No symbols available for trading!")
        return []
    
    logging.info(f"Trading with symbols: {available_symbols}")
    logging.info("="*60)
    
    return available_symbols

def main():
    global current_strategy_manager, strategy_file_path, current_trading_symbols
    
    # Setup logging
    setup_logging()
    logging.info("="*80)
    logging.info("🚀 MT5 AUTO TRADING SYSTEM STARTED")
    logging.info("="*80)
    
    # Load configuration HANYA untuk broker settings
    broker_config = load_config('config/broker.yaml')
    trading_config = load_config('config/trading_config.yaml')
    
    # Initialize MT5
    if not initialize_mt5(broker_config):
        logging.error("Failed to initialize MT5. Exiting...")
        return
    
    # ======================================================================
    # FIX: Initialize News Collector dan start background updater
    # ======================================================================
    logging.info("="*60)
    logging.info("INITIALIZING NEWS COLLECTOR")
    logging.info("="*60)
    
    news_collector = NewsCollector()
    
    # Initial news fetch
    logging.info("Fetching initial news data from MT5 Economic Calendar...")
    initial_news_count = news_collector.update_news(days_ahead=7)
    if initial_news_count > 0:
        logging.info(f"✓ Loaded {initial_news_count} news items from MT5 calendar")
    else:
        logging.warning("No news data fetched from MT5 calendar")
    
    # Start background news updater (updates every 30 minutes)
    news_updater_thread = start_news_updater(interval_minutes=30)
    logging.info("✓ Background news updater started (interval: 30 minutes)")
    
    # Display upcoming high-impact news
    upcoming_news = news_collector.get_upcoming_news(hours=24, impact='High')
    if upcoming_news:
        logging.info("="*60)
        logging.info(f"HIGH IMPACT NEWS - Next 24 Hours ({len(upcoming_news)} events)")
        logging.info("="*60)
        for news in upcoming_news[:5]:  # Show first 5
            logging.info(f"{news['event_time']} | {news['currency']} | {news['title']}")
        if len(upcoming_news) > 5:
            logging.info(f"... and {len(upcoming_news) - 5} more events")
        logging.info("="*60)
    
    # Initialize components
    data_collector = DataCollector(trading_config)
    
    # Load strategy from JSON file
    strategy_file_path = find_strategy_file()
    current_strategy_manager = StrategyManager(strategy_file_path)
    
    # ======================================================================
    # FIX: DAPATKAN SYMBOLS DARI STRATEGY SAJA
    # ======================================================================
    # Get trading symbols FROM STRATEGY ONLY
    current_trading_symbols = get_symbols_from_strategy(current_strategy_manager)
    
    # Display strategy info
    strategy_info = current_strategy_manager.get_strategy_info()
    logging.info("="*80)
    logging.info("📋 LOADED STRATEGY INFO:")
    logging.info(f"Name: {strategy_info['name']}")
    logging.info(f"Philosophy: {strategy_info.get('philosophy', 'N/A')}")
    logging.info(f"Timeframes: {strategy_info.get('timeframes', [])}")
    logging.info(f"Trading Pairs (FROM STRATEGY ONLY): {current_trading_symbols}")
    logging.info("="*80)
    
    # ======================================================================
    # FIX: Save initial strategy state DENGAN SYMBOLS YANG BENAR
    # ======================================================================
    save_strategy_state(
        strategy_name=strategy_info['name'],
        strategy_path=strategy_file_path,
        strategy_info={
            'name': strategy_info['name'],
            'philosophy': strategy_info.get('philosophy', 'N/A'),
            'timeframes': strategy_info.get('timeframes', []),
            'pairs': current_trading_symbols,  # ← Simpan symbols yang benar
            'performance_targets': strategy_info.get('performance_targets', {})
        }
    )
    
    trade_executor = TradeExecutor(broker_config, trading_config, current_strategy_manager)
    
    # ======================================================================
    # FIX: VERIFY SYMBOLS AVAILABILITY IN MT5
    # ======================================================================
    logging.info("="*80)
    logging.info("🔍 VERIFYING SYMBOLS AVAILABILITY IN MT5")
    logging.info("="*80)
    
    verified_symbols = verify_symbols_availability(current_trading_symbols)
    
    if not verified_symbols:
        logging.error("❌ No valid symbols available. Exiting...")
        mt5.shutdown()
        return
    
    # Update dengan symbols yang verified
    current_trading_symbols = verified_symbols
    logging.info(f"✅ Final trading symbols (verified): {current_trading_symbols}")
    
    try:
        logging.info("Starting main trading loop...")
        cycle_count = 0
        
        while True:
            cycle_count += 1
            current_time = datetime.now()
            
            # ======================================================================
            # FIX: Check for strategy reload signal
            # Catatan: reload bisa juga dipicu dari DALAM collect_minute_data.
            # Di sini kita handle sinyal yang muncul di luar collect window,
            # sekaligus update trade_executor & symbols setelah reload manapun.
            # ======================================================================
            was_reloaded = check_strategy_reload_signal()
            if was_reloaded:
                logging.info("🔄 Processing strategy reload signal (top-of-cycle)...")
                reload_strategy()

            # Selalu sync trade_executor & verified symbols di awal setiap cycle,
            # baik reload dipicu dari sini MAUPUN dari dalam collect_minute_data.
            trade_executor.strategy_manager = current_strategy_manager
            trade_executor.refresh_from_strategy()  # re-read risk params from new strategy
            verified_symbols = verify_symbols_availability(current_trading_symbols)
            if verified_symbols:
                if verified_symbols != current_trading_symbols:
                    logging.info(f"✅ Symbols updated: {current_trading_symbols} → {verified_symbols}")
                current_trading_symbols = verified_symbols
            else:
                if was_reloaded:
                    logging.error("❌ New strategy has no valid symbols! Keeping old symbols.")
            
            # Log current strategy at start of each cycle
            logging.info("="*80)
            logging.info(f"📊 CYCLE #{cycle_count} - {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
            logging.info(f"Strategy: {current_strategy_manager.strategy_name}")
            logging.info(f"Trading Symbols (FROM STRATEGY): {current_trading_symbols}")
            logging.info("="*80)
            
            # Step 1: Collect 1-minute tick data
            collect_minute_data(data_collector, current_trading_symbols, duration=60)
            
            # Step 2: Process each symbol
            for symbol in current_trading_symbols:
                try:
                    logging.info(f"\n🔎 Processing {symbol}...")
                    
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
                        
                        logging.info(f"  📊 1-min Average - Bid: {avg_bid:.5f}, Ask: {avg_ask:.5f}, Spread: {avg_spread:.5f}")
                    
                    # Analyze with strategy
                    signal = current_strategy_manager.analyze(symbol, ohlc_data)
                    
                    logging.info(f"  🎯 Signal: {signal['action']} (Confidence: {signal['confidence']}%)")
                    
                    # Log key indicators
                    if signal['indicators']:
                        logging.info(f"  📈 Indicators:")
                        for key, value in signal['indicators'].items():
                            logging.info(f"    {key}: {value:.5f}")
                    
                    # Execute trades based on signal
                    if signal['action'] != 'HOLD':
                        logging.info(safe_log(f"  ⚡ Executing {signal['action']} order..."))
                        success = trade_executor.execute_signal(symbol, signal)
                        if success:
                            logging.info(safe_log(f"  ✅ Order executed successfully"))
                            # Log order details
                            positions = mt5.positions_get(symbol=symbol)
                            if positions:
                                latest = positions[-1]
                                logging.info(f"  📝 Order Details:")
                                logging.info(f"    Ticket: {latest.ticket}")
                                logging.info(f"    Volume: {latest.volume}")
                                logging.info(f"    Price: {latest.price_open}")
                                logging.info(f"    SL: {latest.sl}")
                                logging.info(f"    TP: {latest.tp}")
                        else:
                            logging.warning(safe_log(f"  ❌ Order execution failed"))
                    else:
                        logging.info(safe_log(f"  ⏸️  No action taken (HOLD)"))
                    
                except Exception as e:
                    logging.error(f"❌ Error processing {symbol}: {str(e)}")
                    import traceback
                    logging.error(traceback.format_exc())
            
            # Step 3: Manage existing positions
            logging.info("\n💰 Managing existing positions...")
            trade_executor.manage_positions()
            
            # Step 4: Update news periodically (every 30 minutes)
            if cycle_count % 30 == 0:  # Every 30 cycles (≈30 minutes)
                logging.info("\n📰 Checking for news updates...")
                updated_count = news_collector.update_news(days_ahead=2)
                if updated_count > 0:
                    logging.info(f"✅ Updated {updated_count} news items")
            
            # Short pause before next cycle
            logging.info(f"\n🔄 Cycle #{cycle_count} completed. Waiting for next cycle...")
            time.sleep(1)
            
    except KeyboardInterrupt:
        logging.info("\n🛑 Trading system stopped by user")
    except Exception as e:
        logging.error(f"❌ Unexpected error: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
    finally:
        logging.info("🔌 Shutting down MT5...")
        mt5.shutdown()

        
if __name__ == "__main__":
    main()