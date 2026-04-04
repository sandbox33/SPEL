"""
API HEALTH SENSOR - SPEL
Monitors NewsData, Finnhub, GDELT status in real-time
Graceful fallback if APIs are down
"""

import time
import requests
import json
from typing import Dict, Optional, Tuple
from datetime import datetime
from enum import Enum


class APIStatus(Enum):
    """Status codes for APIs."""
    LIVE = "🟢 LIVE"
    SLOW = "🟡 SLOW"
    OFFLINE = "🔴 OFFLINE"
    UNKNOWN = "⚪ UNKNOWN"


class APISensor:
    """Monitor API health with timeouts and retry logic."""
    
    # Thresholds (in seconds)
    TIMEOUT = 5.0
    SLOW_THRESHOLD = 2.0
    
    def __init__(self):
        """Initialize sensor."""
        self.last_check = {}
        self.status_cache = {
            'newsdata': {'status': APIStatus.UNKNOWN, 'time': 0, 'msg': ''},
            'finnhub': {'status': APIStatus.UNKNOWN, 'time': 0, 'msg': ''},
            'gdelt': {'status': APIStatus.UNKNOWN, 'time': 0, 'msg': ''}
        }
    
    def check_newsdata(self, api_key: Optional[str] = None) -> Dict:
        """
        Check NewsData.io API.
        
        Args:
            api_key: NewsData.io API key
        
        Returns:
            {'status': APIStatus, 'time': response_time, 'msg': message}
        """
        
        if api_key is None:
            api_key = 'demo'  # Fallback
        
        try:
            start = time.time()
            resp = requests.get(
                'https://newsdata.io/api/1/news',
                params={'q': 'NVIDIA', 'apikey': api_key, 'language': 'en'},
                timeout=self.TIMEOUT
            )
            elapsed = time.time() - start
            
            if resp.status_code == 200:
                if elapsed > self.SLOW_THRESHOLD:
                    status = APIStatus.SLOW
                    msg = f"Response time: {elapsed:.2f}s"
                else:
                    status = APIStatus.LIVE
                    msg = f"OK ({elapsed:.2f}s)"
            elif resp.status_code == 429:
                status = APIStatus.SLOW
                msg = "Rate limited (wait before next call)"
            else:
                status = APIStatus.OFFLINE
                msg = f"HTTP {resp.status_code}"
            
        except requests.Timeout:
            status = APIStatus.OFFLINE
            msg = f"Timeout (>{self.TIMEOUT}s)"
            elapsed = self.TIMEOUT
        
        except Exception as e:
            status = APIStatus.OFFLINE
            msg = f"Error: {str(e)[:30]}"
            elapsed = 0
        
        result = {
            'status': status,
            'time': elapsed,
            'msg': msg,
            'timestamp': datetime.now().isoformat()
        }
        
        self.status_cache['newsdata'] = result
        self.last_check['newsdata'] = result
        
        return result
    
    def check_finnhub(self, api_key: Optional[str] = None) -> Dict:
        """Check Finnhub API."""
        
        if api_key is None:
            api_key = 'demo'
        
        try:
            start = time.time()
            resp = requests.get(
                'https://finnhub.io/api/v1/quote',
                params={'symbol': 'NVDA', 'token': api_key},
                timeout=self.TIMEOUT
            )
            elapsed = time.time() - start
            
            if resp.status_code == 200:
                data = resp.json()
                if 'c' in data:  # Close price field
                    if elapsed > self.SLOW_THRESHOLD:
                        status = APIStatus.SLOW
                        msg = f"Response time: {elapsed:.2f}s"
                    else:
                        status = APIStatus.LIVE
                        msg = f"OK - NVDA: ${data['c']:.2f} ({elapsed:.2f}s)"
                else:
                    status = APIStatus.OFFLINE
                    msg = "Invalid response format"
            elif resp.status_code == 429:
                status = APIStatus.SLOW
                msg = "Rate limited"
            else:
                status = APIStatus.OFFLINE
                msg = f"HTTP {resp.status_code}"
            
        except requests.Timeout:
            status = APIStatus.OFFLINE
            msg = f"Timeout (>{self.TIMEOUT}s)"
            elapsed = self.TIMEOUT
        
        except Exception as e:
            status = APIStatus.OFFLINE
            msg = f"Error: {str(e)[:30]}"
            elapsed = 0
        
        result = {
            'status': status,
            'time': elapsed,
            'msg': msg,
            'timestamp': datetime.now().isoformat()
        }
        
        self.status_cache['finnhub'] = result
        self.last_check['finnhub'] = result
        
        return result
    
    def check_gdelt(self) -> Dict:
        """Check GDELT API (free, no key needed)."""
        
        try:
            start = time.time()
            resp = requests.get(
                'http://api.gdeltproject.org/api/v2/tv/tv',
                params={
                    'query': 'Federal Reserve',
                    'mode': 'TimelineVol',
                    'format': 'json'
                },
                timeout=self.TIMEOUT
            )
            elapsed = time.time() - start
            
            if resp.status_code == 200:
                if elapsed > self.SLOW_THRESHOLD:
                    status = APIStatus.SLOW
                    msg = f"Response time: {elapsed:.2f}s"
                else:
                    status = APIStatus.LIVE
                    msg = f"OK ({elapsed:.2f}s)"
            else:
                status = APIStatus.OFFLINE
                msg = f"HTTP {resp.status_code}"
            
        except requests.Timeout:
            status = APIStatus.OFFLINE
            msg = f"Timeout (>{self.TIMEOUT}s)"
            elapsed = self.TIMEOUT
        
        except Exception as e:
            status = APIStatus.OFFLINE
            msg = f"Error: {str(e)[:30]}"
            elapsed = 0
        
        result = {
            'status': status,
            'time': elapsed,
            'msg': msg,
            'timestamp': datetime.now().isoformat()
        }
        
        self.status_cache['gdelt'] = result
        self.last_check['gdelt'] = result
        
        return result
    
    def check_all(self, newsdata_key: Optional[str] = None, 
                  finnhub_key: Optional[str] = None) -> Dict:
        """
        Check all APIs and return summary.
        
        Returns:
            {'newsdata': {...}, 'finnhub': {...}, 'gdelt': {...}}
        """
        
        print("\n" + "=" * 70)
        print("🌐 API HEALTH CHECK")
        print("=" * 70)
        
        results = {
            'newsdata': self.check_newsdata(newsdata_key),
            'finnhub': self.check_finnhub(finnhub_key),
            'gdelt': self.check_gdelt()
        }
        
        # Print report
        for api_name, result in results.items():
            status = result['status'].value
            msg = result['msg']
            time_ms = result['time'] * 1000
            
            print(f"{api_name:12} {status:20} {msg:30} ({time_ms:6.0f}ms)")
        
        print("=" * 70)
        
        # Overall summary
        all_live = all(r['status'] in [APIStatus.LIVE, APIStatus.SLOW] 
                      for r in results.values())
        
        if all_live:
            print("\n✅ All APIs operational")
        else:
            offline_apis = [k for k, v in results.items() 
                           if v['status'] == APIStatus.OFFLINE]
            print(f"\n⚠️  Offline APIs: {', '.join(offline_apis)}")
        
        return results
    
    def get_status_string(self) -> str:
        """Get formatted status string."""
        
        lines = []
        for api_name, status_dict in self.status_cache.items():
            status = status_dict['status'].value
            msg = status_dict['msg']
            lines.append(f"{api_name}: {status} - {msg}")
        
        return "\n".join(lines)
    
    def should_use_api(self, api_name: str) -> bool:
        """Check if API can be used (not offline)."""
        
        status = self.status_cache.get(api_name, {}).get('status', APIStatus.UNKNOWN)
        return status != APIStatus.OFFLINE


class GracefulFallback:
    """Handle API failures gracefully."""
    
    def __init__(self, sensor: APISensor):
        """Initialize fallback handler."""
        self.sensor = sensor
        self.cached_data = {}
    
    def fetch_news(self, api_key: str, ticker: str = 'NVIDIA') -> Optional[list]:
        """
        Fetch news with fallback.
        
        Returns:
            List of news articles or None if all options exhausted
        """
        
        if not self.sensor.should_use_api('newsdata'):
            print("⚠️  NewsData offline, using cached data")
            return self.cached_data.get('news', [])
        
        try:
            resp = requests.get(
                'https://newsdata.io/api/1/news',
                params={'q': ticker, 'apikey': api_key, 'language': 'en'},
                timeout=5
            )
            if resp.status_code == 200:
                articles = resp.json().get('results', [])
                self.cached_data['news'] = articles  # Cache
                return articles
        except:
            pass
        
        return self.cached_data.get('news', [])
    
    def fetch_price(self, api_key: str, symbol: str = 'NVDA') -> Optional[dict]:
        """Fetch price with fallback."""
        
        if not self.sensor.should_use_api('finnhub'):
            print("⚠️  Finnhub offline, using cached price")
            return self.cached_data.get('price', None)
        
        try:
            resp = requests.get(
                'https://finnhub.io/api/v1/quote',
                params={'symbol': symbol, 'token': api_key},
                timeout=5
            )
            if resp.status_code == 200:
                price_data = resp.json()
                self.cached_data['price'] = price_data  # Cache
                return price_data
        except:
            pass
        
        return self.cached_data.get('price', None)


# =====================================================================
# DEMO
# =====================================================================

if __name__ == "__main__":
    
    print("\n" + "=" * 70)
    print("API SENSOR DEMO - SOCIO-POLITICAL ENTROPY LOSS")
    print("=" * 70)
    
    # Create sensor
    sensor = APISensor()
    
    # Check all APIs
    results = sensor.check_all()
    
    # Show status
    print("\nFormatted Status:")
    print(sensor.get_status_string())
    
    # Fallback example
    print("\n" + "=" * 70)
    print("GRACEFUL FALLBACK TEST")
    print("=" * 70)
    
    fallback = GracefulFallback(sensor)
    
    print("\nAttempting to fetch news...")
    news = fallback.fetch_news("demo_key")
    print(f"News articles: {len(news) if news else 0}")
    
    print("\nAttempting to fetch price...")
    price = fallback.fetch_price("demo_key")
    print(f"Price data: {price}")
    
    print("\n✅ API Sensor demo complete")

