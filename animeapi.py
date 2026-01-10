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

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Base URL for the anime site
BASE_URL = "http://animesalt.top"

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

# Endpoint 1: Homepage data
@app.route('/api/home', methods=['GET'])
def get_homepage_data():
    """
    Scrapes anime data from all sections on the animesalt.cc homepage.
    """
    url = BASE_URL
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Error fetching the URL: {e}"}), 500

    soup = BeautifulSoup(response.text, 'html.parser')
    all_anime_data = {}

    # Scrape "Most-Watched" Sections
    most_watched_titles = soup.find_all('h3', class_='section-title', string=lambda text: text and "Most-Watched" in text)
    
    for title_tag in most_watched_titles:
        section_name = title_tag.get_text(strip=True)
        content_container = title_tag.find_next_sibling('div', class_='aa-cn')
        if not content_container:
            continue
            
        chart_content = content_container.find('div', class_='chart-content')
        if not chart_content:
            continue

        items = chart_content.find_all('div', class_='chart-item')
        section_data = []
        for item in items:
            try:
                title = item.find('div', class_='chart-title').get_text(strip=True)
                link = item.find('a', class_='chart-poster')['href']
                link = remove_base_url(link)  # Remove base URL
                image = item.find('img')['data-src']
                if image.startswith('//'):
                    image = 'https:' + image
                section_data.append({'title': title, 'link': link, 'image': image})
            except Exception:
                continue
        
        if section_data:
            all_anime_data[section_name] = section_data

    # Scrape other Swiper-based Sections
    widget_sections = soup.find_all('section', class_=lambda c: c and 'widget' in c and ('widget_list_episodes' in c or 'widget_list_movies_series' in c))
    
    for section in widget_sections:
        title_tag = section.find('h3', class_='section-title')
        if not title_tag:
            continue
        section_name = title_tag.get_text(strip=True)

        swiper_wrapper = section.find('div', class_='swiper-wrapper')
        if not swiper_wrapper:
            continue

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
                    link = remove_base_url(link)  # Remove base URL

                img_tag = list_item.find('img')
                image = img_tag['data-src'] if img_tag and img_tag.has_attr('data-src') else "N/A"
                if image.startswith('//'):
                    image = 'https:' + image
                
                # Get episode information ONLY for "Fresh Drops" section
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
        
        if section_data:
            all_anime_data[section_name] = section_data

    return jsonify(all_anime_data)

# Endpoint 2: Search anime
@app.route('/api/search', methods=['GET'])
def search_anime():
    """
    Searches for anime based on a query parameter.
    """
    search_query = request.args.get('q')
    if not search_query:
        return jsonify({"error": "Missing search query parameter 'q'"}), 400
    
    encoded_query = urllib.parse.quote_plus(search_query)
    search_url = f"{BASE_URL}?s={encoded_query}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Error fetching the URL: {e}"}), 500

    soup = BeautifulSoup(response.text, 'html.parser')
    results_container = soup.select('ul.post-lst > li')
    scraped_data = []

    for item in results_container:
        try:
            title_element = item.find('h2', class_='entry-title')
            title = title_element.text.strip() if title_element else 'N/A'

            link_element = item.find('a', class_='lnk-blk')
            link = link_element['href'] if link_element else 'N/A'
            link = remove_base_url(link)  # Remove base URL

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
            
    return jsonify({"results": scraped_data, "count": len(scraped_data)})

# Endpoint 3: Anime info
@app.route('/api/anime/<path:url_path>', methods=['GET'])
def get_anime_info(url_path):
    """
    Gets detailed information about an anime including episodes.
    """
    full_url = add_base_url(url_path)  # Add base URL for internal use
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': full_url
    }

    try:
        response = requests.get(full_url, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Error fetching the URL: {e}"}), 500

    soup = BeautifulSoup(response.text, 'html.parser')

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
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_index = {
            executor.submit(
                get_episodes_for_season, 
                BASE_URL, 
                button.get('data-post'), 
                button.get('data-season'), 
                headers
            ): i for i, button in enumerate(season_buttons) 
            if button.get('data-post') and button.get('data-season')
        }
        
        season_results = [None] * len(future_to_index)

        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            try:
                season_results[index] = future.result()
            except Exception as e:
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
            'url': remove_base_url(ep_url),  # Remove base URL
            'image_url': ep_image_url
        })

    # Assemble Final Data
    anime_data = {
        'title': title,
        'url': url_path,  # Already relative
        'image_url': image_url,
        'languages': languages,
        'total_episodes': len(all_episodes),
        'episodes': all_episodes
    }

    return jsonify(anime_data)

def get_episodes_for_season(base_url, post_id, season_num, headers):
    """
    Fetches the HTML for a specific season's episodes via an AJAX call.
    """
    ajax_url = f"{base_url}/wp-admin/admin-ajax.php"
    params = {'action': 'action_select_season', 'season': season_num, 'post': post_id}
    try:
        response = requests.get(ajax_url, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        return soup.find_all('li')
    except requests.exceptions.RequestException:
        return []

# Endpoint 4: Streaming links
@app.route('/api/stream/<path:url_path>', methods=['GET'])
def get_streaming_links(url_path):
    """
    Extracts streaming links from an episode or movie page.
    """
    # FIX: Correctly handle movie URLs without prepending 'episode/'
    # If the path is for a movie, use it as is.
    # If it's already a full episode path, use it as is.
    # Otherwise, assume it's a simple episode slug and prepend 'episode/'.
    if url_path.startswith('movies/') or url_path.startswith('episode/'):
        # It's already a correctly formatted path for a movie or episode
        final_path = url_path
    else:
        # It's a simple slug, so we assume it's an episode
        final_path = f"episode/{url_path}"
    
    source_url = add_base_url(final_path)
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
    }

    try:
        response = requests.get(source_url, headers=headers, timeout=15)
        response.raise_for_status()
        html_content = response.text
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Error fetching the URL: {e}"}), 500

    # Search the main HTML page
    html_streams = find_urls_in_text(html_content, source_url)

    # Find and scan all JavaScript files
    js_file_urls = find_js_file_urls(html_content, source_url)
    
    js_streams = []
    for js_url in js_file_urls:
        try:
            js_response = requests.get(js_url, headers=headers, timeout=10)
            js_response.raise_for_status()
            found_in_js = find_urls_in_text(js_response.text, source_url)
            if found_in_js:
                js_streams.extend(found_in_js)
        except requests.exceptions.RequestException:
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
        "episode_url": url_path,  # Return the original requested path
        "m3u8_links": m3u8_links,
        "video_links": video_links,
        "iframes": found_iframes,
        "total_streams": len(m3u8_links) + len(video_links) + len(found_iframes)
    }
    
    return jsonify(result)

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

# Health check endpoint
@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "timestamp": time.time()})

# Root endpoint with API documentation
@app.route('/')
def api_documentation():
    return jsonify({
        "title": "Anime server",
        "version": "1.0.0",
        "base_url": BASE_URL,
        "note": "All URLs in responses are relative to the base URL",
        "endpoints": {
            "/api/home": "Get homepage data with most-watched and other sections",
            "/api/search?q=<query>": "Search for anime by name",
            "/api/anime/<path>": "Get detailed information about an anime including episodes",
            "/api/stream/<path>": "Get streaming links for an episode or movie",
            "/api/health": "Health check endpoint"
        },
        "examples": {
            "search": "/api/search?q=naruto",
            "anime_info": "/api/anime/series/naruto-shippuden",
            "streaming_links_episode": "/api/stream/naruto-shippuden-1x1",
            "streaming_links_movie": "/api/stream/movies/demon-slayer-kimetsu-no-yaiba-infinity-castle"
        }
    })

# This is the key change for Render deployment
if __name__ == '__main__':
    # Get port from environment variable (Render sets this automatically)
    port = int(os.environ.get('PORT', 5000))
    # Listen on all interfaces (0.0.0.0) instead of just localhost
    app.run(host='0.0.0.0', port=port)
