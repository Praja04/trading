"""
Display Upcoming High-Impact News
Quick utility to view upcoming forex news events
"""
import sys
import os
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.news_collector import NewsCollector

def print_separator(char="=", length=80):
    print(char * length)

def format_event_time(event_time_str):
    """Format event time for display"""
    try:
        event_time = datetime.strptime(event_time_str, '%Y-%m-%d %H:%M:%S')
        now = datetime.now()
        
        # Check if today, tomorrow, or specific date
        if event_time.date() == now.date():
            date_str = "TODAY"
        elif event_time.date() == (now.date()):
            date_str = "TOMORROW"
        else:
            date_str = event_time.strftime('%a %m/%d')
        
        time_str = event_time.strftime('%H:%M')
        
        # Calculate time until event
        time_diff = event_time - now
        hours_until = int(time_diff.total_seconds() / 3600)
        
        if hours_until < 0:
            time_until = "PAST"
        elif hours_until == 0:
            minutes_until = int(time_diff.total_seconds() / 60)
            time_until = f"in {minutes_until}m"
        elif hours_until < 24:
            time_until = f"in {hours_until}h"
        else:
            days_until = int(hours_until / 24)
            time_until = f"in {days_until}d"
        
        return f"[{date_str} {time_str}]", time_until
        
    except Exception as e:
        return event_time_str, ""

def display_news(hours=48, impact_filter=None):
    """Display upcoming news"""
    
    print()
    print_separator("=")
    print("UPCOMING FOREX NEWS EVENTS")
    print_separator("=")
    print()
    
    try:
        nc = NewsCollector()
        
        # Get upcoming news
        if impact_filter:
            news = nc.get_upcoming_news(hours=hours, impact=impact_filter)
            impact_text = f"{impact_filter.upper()} IMPACT"
        else:
            news = nc.get_upcoming_news(hours=hours)
            impact_text = "ALL IMPACTS"
        
        if not news:
            print(f"No {impact_text.lower()} news events found for the next {hours} hours.")
            print()
            return
        
        print(f"Showing {len(news)} {impact_text} events for the next {hours} hours")
        print()
        
        # Group by impact level
        high_impact = [n for n in news if n['impact'] == 'High']
        medium_impact = [n for n in news if n['impact'] == 'Medium']
        low_impact = [n for n in news if n['impact'] == 'Low']
        
        # Display HIGH impact first
        if high_impact and (not impact_filter or impact_filter == 'High'):
            print_separator("-")
            print(f"HIGH IMPACT EVENTS ({len(high_impact)})")
            print_separator("-")
            
            for event in high_impact:
                time_str, time_until = format_event_time(event['event_time'])
                
                title = event['title']
                if len(title) > 50:
                    title = title[:47] + "..."
                
                print(f"{time_str:20} {event['currency']:3} | {title}")
                
                if time_until:
                    print(f"{'':20} ⏰  {time_until}", end="")
                
                if event['forecast'] or event['previous']:
                    print(f" | Forecast: {event['forecast'] or 'N/A':>8} | Previous: {event['previous'] or 'N/A':>8}")
                else:
                    print()
                
                print()
        
        # Display MEDIUM impact
        if medium_impact and (not impact_filter or impact_filter == 'Medium'):
            print_separator("-")
            print(f"MEDIUM IMPACT EVENTS ({len(medium_impact)})")
            print_separator("-")
            
            for event in medium_impact[:10]:  # Show only first 10
                time_str, time_until = format_event_time(event['event_time'])
                
                title = event['title']
                if len(title) > 50:
                    title = title[:47] + "..."
                
                print(f"{time_str:20} {event['currency']:3} | {title}")
                
                if time_until and event['forecast']:
                    print(f"{'':20} ⏰  {time_until} | Forecast: {event['forecast']}")
                
                print()
            
            if len(medium_impact) > 10:
                print(f"... and {len(medium_impact) - 10} more medium impact events")
                print()
        
        # Display LOW impact (summary only)
        if low_impact and not impact_filter:
            print_separator("-")
            print(f"LOW IMPACT EVENTS ({len(low_impact)}) - Summary")
            print_separator("-")
            print(f"There are {len(low_impact)} low impact events scheduled.")
            print("Use --impact Low to see full details.")
            print()
        
        # Summary
        print_separator("=")
        print("SUMMARY")
        print_separator("=")
        print(f"Total events:  {len(news)}")
        if not impact_filter:
            print(f"  High impact:   {len(high_impact)}")
            print(f"  Medium impact: {len(medium_impact)}")
            print(f"  Low impact:    {len(low_impact)}")
        print()
        
        # Show currencies
        currencies = {}
        for event in news:
            curr = event['currency']
            if curr:
                currencies[curr] = currencies.get(curr, 0) + 1
        
        if currencies:
            print("Events by currency:")
            for curr, count in sorted(currencies.items(), key=lambda x: x[1], reverse=True)[:10]:
                print(f"  {curr}: {count}")
        
        print_separator("=")
        print()
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        print()

def display_today_news():
    """Display today's high impact news"""
    print()
    print_separator("=")
    print("TODAY'S HIGH IMPACT NEWS")
    print_separator("=")
    print()
    
    try:
        nc = NewsCollector()
        news = nc.get_high_impact_news_today()
        
        if not news:
            print("No high impact news events scheduled for today.")
            print()
            return
        
        print(f"Found {len(news)} high impact events today")
        print()
        
        for event in news:
            time_str, time_until = format_event_time(event['event_time'])
            
            print(f"{time_str:20} {event['currency']:3} | {event['title']}")
            
            forecast_str = event['forecast'] or 'N/A'
            previous_str = event['previous'] or 'N/A'
            actual_str = event['actual'] or 'N/A'
            
            print(f"{'':20} Forecast: {forecast_str:>8} | Previous: {previous_str:>8} | Actual: {actual_str:>8}")
            
            if time_until:
                print(f"{'':20} ⏰  {time_until}")
            
            print()
        
        print_separator("=")
        print()
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        print()

def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Display upcoming forex news events')
    parser.add_argument('--hours', type=int, default=48, help='Hours to look ahead (default: 48)')
    parser.add_argument('--impact', choices=['High', 'Medium', 'Low'], help='Filter by impact level')
    parser.add_argument('--today', action='store_true', help='Show only today\'s high impact news')
    
    args = parser.parse_args()
    
    if args.today:
        display_today_news()
    else:
        display_news(hours=args.hours, impact_filter=args.impact)

if __name__ == "__main__":
    main()