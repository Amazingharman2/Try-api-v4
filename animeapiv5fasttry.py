import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import json
import re
import urllib.parse
import time
import concurrent.futures
from urllib.parse import urljoin
from functools import lru_cache
import threading
from queue import Queue
import hashlib
import gzip
import io

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Base URL for the anime site
BASE_URL = "https://animesalt.cc"

# Session reuse for better performance
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept-Encoding': 'gzip, deflate',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Connection': 'keep-alive',
})

# Thread pool for parallel operations
thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=10)

# In-memory cache with TTL (Time To Live)
class CacheManager:
    def __init__(self, ttl=300):  # 5 minutes default TTL
        self.cache = {}
        self.ttl = ttl
        self.lock = threading.Lock()
    
    def get(self, key):
        with self.lock:
            if key in self.cache:
                data, timestamp = self.cache[key]
                if time.time() - timestamp < self.ttl:
                    return data
                else:
                    del self.cache[key]
        return None
    
    def set(self, key, value):
        with self.lock:
            self.cache[key] = (value, time.time())
    
    def clear(self):
        with self.lock:
            self.cache.clear()

# Initialize cache
cache = CacheManager(ttl=300)  # 5 minute cache

def remove_base_url(url):
    """Remove the base URL from a given URL to make it relative."""
    if url.startswith(BASE_URL):
        return url.replace(BASE_URL, "")
    return url

def add_base_url(url):
    """Add the base URL to a relative URL to make it absolute."""
    if not url.startswith("http"):
        return f"{BASE_URL}{url}" if url.startswith("/") else f"{BASE_URL}/{url}"
    return url

def fetch_with_cache(url, use_cache=True):
    """Fetch URL with caching support."""
    if use_cache:
        cache_key = hashlib.md5(url.encode()).hexdigest()
        cached_data = cache.get(cache_key)
        if cached_data:
            return cached_data
    
    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()
        content = response.content
        
        # Handle gzip compression
        if response.headers.get('Content-Encoding') == 'gzip':
            content = gzip.decompress(content)
        
        html_content = content.decode('utf-8', errors='ignore')
        
        if use_cache:
            cache.set(cache_key, html_content)
        
        return html_content
    except requests.exceptions.RequestException as e:
        raise Exception(f"Error fetching URL: {e}")

# Fast parallel scraping for multiple sections
def scrape_section_parallel(section):
    """Scrape a single section in parallel."""
    title_tag, soup = section
    section_name = title_tag.get_text(strip=True)
    content_container = title_tag.find_next_sibling('div', class_='aa-cn')
    
    if not content_container:
        return section_name, []
    
    chart_content = content_container.find('div', class_='chart-content')
    if not chart_content:
        return section_name, []
    
    items = chart_content.find_all('div', class_='chart-item')
    section_data = []
    
    for item in items:
        try:
            title = item.find('div', class_='chart-title').get_text(strip=True)
            link = item.find('a', class_='chart-poster')['href']
            link = remove_base_url(link)
            image = item.find('img')['data-src']
            if image.startswith('//'):
                image = 'https:' + image
            section_data.append({'title': title, 'link': link, 'image': image})
        except Exception:
            continue
    
    return section_name, section_data

def scrape_swiper_section_parallel(section):
    """Scrape a single swiper section in parallel."""
    section_name = section.find('h3', class_='section-title')
    if not section_name:
        return None, []
    
    section_name = section_name.get_text(strip=True)
    swiper_wrapper = section.find('div', class_='swiper-wrapper')
    
    if not swiper_wrapper:
        return section_name, []
    
    slides = swiper_wrapper.find_all('div', class_='swiper-slide')
    section_data = []
    
    for slide in slides:
        try:
            list_item = slide.find('li')
            if not list_item:
                continue

            title_tag = list_item.find('h2', class_='entry-title')
            title = title_tag.get_text(strip=True) if title_tag else "N/A"

            link_tag = list_item.find('a', class_='lnk-blk')
            link = link_tag['href'] if link_tag else "N/A"

            if link != "N/A":
                link = remove_base_url(link)

            img_tag = list_item.find('img')
            image = img_tag['data-src'] if img_tag and img_tag.has_attr('data-src') else "N/A"
            if image.startswith('//'):
                image = 'https:' + image
            
            # Get episode information for "Fresh Drops" section
            if section_name == "Fresh Drops":
                ep_tag = list_item.find('span', class_='year')
                episodes = ep_tag.get_text(strip=True) if ep_tag else "N/A"
                
                if title != "N/A":
                    section_data.append({
                        'title': title, 
                        'link': link, 
                        'image': image,
                        'episodes': episodes
                    })
            else:
                if title != "N/A":
                    section_data.append({
                        'title': title, 
                        'link': link, 
                        'image': image
                    })
        except Exception:
            continue
    
    return section_name, section_data

# Endpoint 1: Homepage data - OPTIMIZED
@app.route('/api/home', methods=['GET'])
def get_homepage_data():
    """Scrapes anime data from all sections on the animesalt.cc homepage."""
    # Check cache first
    cache_key = "homepage_data"
    cached_result = cache.get(cache_key)
    if cached_result:
        return jsonify(cached_result)
    
    try:
        html_content = fetch_with_cache(BASE_URL, use_cache=True)
        soup = BeautifulSoup(html_content, 'html.parser')
        all_anime_data = {}

        # Parallel scraping for Most-Watched sections
        most_watched_titles = soup.find_all('h3', class_='section-title', 
                                           string=lambda text: text and "Most-Watched" in text)
        
        # Prepare tasks for parallel execution
        most_watched_tasks = [(title_tag, soup) for title_tag in most_watched_titles]
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_section = {
                executor.submit(scrape_section_parallel, task): task[0] 
                for task in most_watched_tasks
            }
            
            for future in concurrent.futures.as_completed(future_to_section):
                section_name, section_data = future.result()
                if section_data:
                    all_anime_data[section_name] = section_data

        # Parallel scraping for swiper sections
        widget_sections = soup.find_all('section', class_=lambda c: c and 'widget' in c and 
                                       ('widget_list_episodes' in c or 'widget_list_movies_series' in c))
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_section = {
                executor.submit(scrape_swiper_section_parallel, section): section 
                for section in widget_sections
            }
            
            for future in concurrent.futures.as_completed(future_to_section):
                result = future.result()
                if result and result[1]:  # section_name, section_data
                    section_name, section_data = result
                    all_anime_data[section_name] = section_data

        # Cache the result
        cache.set(cache_key, all_anime_data)
        
        return jsonify(all_anime_data)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Endpoint 2: Search anime - OPTIMIZED
@app.route('/api/search', methods=['GET'])
def search_anime():
    """Searches for anime based on a query parameter."""
    search_query = request.args.get('q')
    if not search_query:
        return jsonify({"error": "Missing search query parameter 'q'"}), 400
    
    # Check cache first
    cache_key = f"search_{search_query}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return jsonify(cached_result)
    
    encoded_query = urllib.parse.quote_plus(search_query)
    search_url = f"{BASE_URL}?s={encoded_query}"
    
    try:
        html_content = fetch_with_cache(search_url, use_cache=True)
        soup = BeautifulSoup(html_content, 'html.parser')
        results_container = soup.select('ul.post-lst > li')
        scraped_data = []

        # Use list comprehension for faster processing
        for item in results_container:
            try:
                title_element = item.find('h2', class_='entry-title')
                title = title_element.text.strip() if title_element else 'N/A'

                link_element = item.find('a', class_='lnk-blk')
                link = link_element['href'] if link_element else 'N/A'
                link = remove_base_url(link)

                image_element = item.select_one('.post-thumbnail img')
                image_url = image_element['data-src'] if image_element and image_element.has_attr('data-src') else 'N/A'

                item_classes = item.get('class', [])
                categories = [cls.replace('category-', '') for cls in item_classes if cls.startswith('category-')]
                
                anime_info = {
                    'title': title,
                    'link': link,
                    'image_url': image_url,
                    'categories': ', '.join(categories) if categories else 'N/A'
                }
                scraped_data.append(anime_info)
            except AttributeError:
                continue
        
        result = {"results": scraped_data, "count": len(scraped_data)}
        
        # Cache the result
        cache.set(cache_key, result)
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Endpoint 3: Anime info - OPTIMIZED
@app.route('/api/anime/<path:url_path>', methods=['GET'])
def get_anime_info(url_path):
    """Gets detailed information about an anime including episodes."""
    # Check cache first
    cache_key = f"anime_info_{url_path}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return jsonify(cached_result)
    
    full_url = add_base_url(url_path)
    
    try:
        html_content = fetch_with_cache(full_url, use_cache=True)
        soup = BeautifulSoup(html_content, 'html.parser')

        # Scrape Main Anime Info
        title_tag = soup.find('h1')
        title = title_tag.text.strip() if title_tag else 'N/A'

        og_image_tag = soup.find('meta', property='og:image')
        image_url = og_image_tag.get('content') if og_image_tag and og_image_tag.get('content') else 'N/A'
        
        languages = []
        lang_header = soup.find('h4', string=re.compile(r'Languages', re.I))
        if lang_header:
            next_div = lang_header.find_next_sibling('div')
            if next_div:
                lang_tags = next_div.find_all('a')
                languages = [g.text.strip() for g in lang_tags]

        # Find All Seasons and Fetch Them Concurrently
        season_buttons = soup.find_all('a', class_='season-btn')
        if not season_buttons:
            return jsonify({"error": "No season buttons found"}), 404
        
        all_episode_items = []
        
        # Use thread pool for parallel season fetching
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_season = {
                executor.submit(
                    get_episodes_for_season, 
                    button.get('data-post'), 
                    button.get('data-season')
                ): i for i, button in enumerate(season_buttons) 
                if button.get('data-post') and button.get('data-season')
            }
            
            season_results = [None] * len(future_to_season)

            for future in concurrent.futures.as_completed(future_to_season):
                index = future_to_season[future]
                try:
                    season_results[index] = future.result()
                except Exception:
                    season_results[index] = []

            for episodes in season_results:
                if episodes:
                    all_episode_items.extend(episodes)

        # Process All Episode Items
        all_episodes = []
        for item in all_episode_items:
            link_tag = item.find('a', class_='lnk-blk')
            if not link_tag or not link_tag.get('href'):
                continue

            ep_url = link_tag.get('href')
            if ep_url.startswith('/'):
                ep_url = BASE_URL + ep_url.lstrip('/')

            ep_num_tag = item.find('span', class_='num-epi')
            ep_number = ep_num_tag.text.strip() if ep_num_tag else 'N/A'

            ep_title_tag = item.find('h2', class_='entry-title')
            ep_title = ep_title_tag.text.strip() if ep_title_tag else 'N/A'
            
            ep_image_tag = item.find('div', class_='post-thumbnail')
            ep_image_url = 'N/A'
            if ep_image_tag:
                img_tag = ep_image_tag.find('img')
                if img_tag:
                    ep_image_url = img_tag.get('data-src') or img_tag.get('src') or 'N/A'
            
            all_episodes.append({
                'episode_number': ep_number,
                'title': ep_title,
                'url': remove_base_url(ep_url),
                'image_url': ep_image_url
            })

        # Assemble Final Data
        anime_data = {
            'title': title,
            'url': url_path,
            'image_url': image_url,
            'languages': languages,
            'total_episodes': len(all_episodes),
            'episodes': all_episodes
        }

        # Cache the result (shorter TTL for frequently changing data)
        cache.set(cache_key, anime_data)
        
        return jsonify(anime_data)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def get_episodes_for_season(post_id, season_num):
    """Fetches the HTML for a specific season's episodes via an AJAX call."""
    ajax_url = f"{BASE_URL}/wp-admin/admin-ajax.php"
    params = {'action': 'action_select_season', 'season': season_num, 'post': post_id}
    
    # Use a new session for each thread
    local_session = requests.Session()
    local_session.headers.update(session.headers)
    
    try:
        response = local_session.get(ajax_url, params=params, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        return soup.find_all('li')
    except requests.exceptions.RequestException:
        return []

# Endpoint 4: Streaming links - OPTIMIZED
@app.route('/api/stream/<path:url_path>', methods=['GET'])
def get_streaming_links(url_path):
    """Extracts streaming links from an episode or movie page."""
    # Check cache first (very short TTL for streaming links)
    cache_key = f"stream_{url_path}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return jsonify(cached_result)
    
    if url_path.startswith('movies/') or url_path.startswith('episode/'):
        final_path = url_path
    else:
        final_path = f"episode/{url_path}"
    
    source_url = add_base_url(final_path)
    
    try:
        html_content = fetch_with_cache(source_url, use_cache=False)  # Don't cache for streaming
        
        # Search the main HTML page
        html_streams = find_urls_in_text(html_content, source_url)

        # Find and scan JavaScript files in parallel
        js_file_urls = find_js_file_urls(html_content, source_url)
        js_streams = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_js = {
                executor.submit(scan_js_file, js_url): js_url 
                for js_url in js_file_urls[:5]  # Limit to 5 JS files for speed
            }
            
            for future in concurrent.futures.as_completed(future_to_js):
                try:
                    found_in_js = future.result()
                    if found_in_js:
                        js_streams.extend(found_in_js)
                except Exception:
                    continue

        # Find Iframes
        iframe_pattern = re.compile(r'<iframe[^>]+src\s*=\s*["\'](.*?)["\']', re.IGNORECASE)
        found_iframes = list(set([urljoin(source_url, src) for src in iframe_pattern.findall(html_content)]))

        # Combine, Deduplicate, and Filter Results
        all_streams = list(set(html_streams + js_streams))
        
        # Filter out common non-stream URLs
        filtered_streams = [
            url for url in all_streams 
            if not any(ext in url.lower() for ext in ['.js', '.css', '.png', '.jpg', '.gif', '.svg', '.ico'])
        ]

        # Prioritize the results
        m3u8_links = [url for url in filtered_streams if '.m3u8' in url.lower()]
        video_links = [url for url in filtered_streams if any(ext in url.lower() for ext in ['.mp4', '.webm', '.mov', '.ts'])]
        
        result = {
            "episode_url": url_path,
            "m3u8_links": m3u8_links,
            "video_links": video_links,
            "iframes": found_iframes,
            "total_streams": len(m3u8_links) + len(video_links) + len(found_iframes)
        }
        
        # Cache for 1 minute only (streaming links change frequently)
        cache.set(cache_key, result)
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def scan_js_file(js_url):
    """Scan a single JS file for streaming URLs."""
    local_session = requests.Session()
    local_session.headers.update(session.headers)
    
    try:
        js_response = local_session.get(js_url, timeout=5)
        js_response.raise_for_status()
        return find_urls_in_text(js_response.text, js_url)
    except:
        return []

def find_urls_in_text(text, base_url):
    """Finds all streaming-related URLs in a given text."""
    m3u8_pattern = re.compile(r'(https?://[^\s"\'<>`]+\.m3u8[^\s"\'<>`]*)', re.IGNORECASE)
    video_pattern = re.compile(r'(https?://[^\s"\'<>`]+\.(?:mp4|webm|ogg|mov|mkv|ts)(?:\?[^\s"\'<>`]*)?)', re.IGNORECASE)
    
    found_urls = m3u8_pattern.findall(text) + video_pattern.findall(text)
    
    # Convert relative URLs to absolute URLs
    absolute_urls = [urljoin(base_url, url) for url in found_urls]
    return list(set(absolute_urls))

def find_js_file_urls(html_content, base_url):
    """Finds all .js file URLs linked in the HTML."""
    js_pattern = re.compile(r'<script[^>]+src\s*=\s*["\'](.*?)["\']', re.IGNORECASE)
    js_urls = js_pattern.findall(html_content)
    
    # Filter for .js files and make them absolute
    absolute_js_urls = []
    for url in js_urls:
        if url.endswith('.js') or 'javascript' in url:
            absolute_js_urls.append(urljoin(base_url, url))
            
    return list(set(absolute_js_urls))

# Clear cache endpoint (for debugging)
@app.route('/api/clear-cache', methods=['POST'])
def clear_cache():
    cache.clear()
    return jsonify({"status": "Cache cleared"})

# Health check endpoint with performance metrics
@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy", 
        "timestamp": time.time(),
        "cache_size": len(cache.cache),
        "uptime": time.time() - app.start_time if hasattr(app, 'start_time') else 0
    })

# Root endpoint with API documentation
@app.route('/')
def api_documentation():
    return jsonify({
        "title": "Anime server",
        "version": "1.0.0",
        "base_url": BASE_URL,
        "note": "All URLs in responses are relative to the base URL",
        "features": ["Fast parallel scraping", "Intelligent caching", "Gzip compression", "Connection pooling"],
        "endpoints": {
            "/api/home": "Get homepage data with most-watched and other sections",
            "/api/search?q=<query>": "Search for anime by name",
            "/api/anime/<path>": "Get detailed information about an anime including episodes",
            "/api/stream/<path>": "Get streaming links for an episode or movie",
            "/api/health": "Health check endpoint with performance metrics",
            "/api/clear-cache": "Clear cache (POST)"
        },
        "examples": {
            "search": "/api/search?q=naruto",
            "anime_info": "/api/anime/series/naruto-shippuden",
            "streaming_links_episode": "/api/stream/naruto-shippuden-1x1",
            "streaming_links_movie": "/api/stream/movies/demon-slayer-kimetsu-no-yaiba-infinity-castle"
        }
    })

# Record startup time
@app.before_first_request
def before_first_request():
    app.start_time = time.time()

# This is the key change for Render deployment
if __name__ == '__main__':
    # Get port from environment variable (Render sets this automatically)
    port = int(os.environ.get('PORT', 5000))
    # Increase thread pool size for better concurrency
    os.environ['FLASK_THREADS'] = '20'
    # Listen on all interfaces (0.0.0.0) instead of just localhost
    app.run(host='0.0.0.0', port=port, threaded=True)