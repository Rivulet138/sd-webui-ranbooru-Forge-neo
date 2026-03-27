from io import BytesIO
import html
import random
import requests
import modules.scripts as scripts
from modules.scripts import OnComponent
import gradio as gr
import os
from PIL import Image
import numpy as np
import importlib
import json
try:
    import requests_cache
    HAS_REQUESTS_CACHE = True
except Exception:
    requests_cache = None
    HAS_REQUESTS_CACHE = False

from modules.processing import process_images, StableDiffusionProcessingImg2Img
from modules import shared
from modules.sd_hijack import model_hijack
from modules import deepbooru
try:
    from modules.ui_components import InputAccordion
except Exception:
    InputAccordion = gr.Accordion



extension_root = scripts.basedir()
user_data_dir = os.path.join(extension_root, 'user')
user_search_dir = os.path.join(user_data_dir, 'search')
user_remove_dir = os.path.join(user_data_dir, 'remove')
user_credentials_dir = os.path.join(user_data_dir, 'credentials')
user_cache_dir = os.path.join(user_data_dir, 'cache')
os.makedirs(user_search_dir, exist_ok=True)
os.makedirs(user_remove_dir, exist_ok=True)
os.makedirs(user_credentials_dir, exist_ok=True)
os.makedirs(user_cache_dir, exist_ok=True)

# ─── Global constant: default bad tags (with underscore and space variants) ───
DEFAULT_BAD_TAGS = [
    # underscore variants
    'mixed-language_text', 'watermark', 'text', 'english_text', 'speech_bubble',
    'signature', 'artist_name', 'censored', 'bar_censor', 'translation',
    'twitter_username', 'twitter_logo', 'patreon_username', 'commentary_request',
    'tagme', 'commentary', 'character_name', 'mosaic_censoring', 'instagram_username',
    'text_focus', 'english_commentary', 'comic', 'translation_request', 'fake_text',
    'translated', 'paid_reward_available', 'thought_bubble', 'multiple_views',
    'silent_comic', 'out-of-frame_censoring', 'symbol-only_commentary', '3koma',
    '2koma', 'character_watermark', 'spoken_question_mark', 'japanese_text',
    'spanish_text', 'language_text', 'fanbox_username', 'commission', 'original',
    'ai_generated', 'stable_diffusion', 'tagme_(artist)', 'text_bubble', 'qr_code',
    'chinese_commentary', 'korean_text', 'partial_commentary', 'chinese_text',
    'copyright_request', 'heart_censor', 'censored_nipples', 'page_number', 'scan',
    'fake_magazine_cover', 'korean_commentary',
    'sample_watermark', 'copyright_notice', 'copyright_name', 'album_cover', 'company_name',
    # space variants
    'mixed language text', 'english text', 'speech bubble', 'artist name', 'bar censor',
    'twitter username', 'twitter logo', 'patreon username', 'commentary request',
    'character name', 'mosaic censoring', 'instagram username', 'text focus',
    'english commentary', 'translation request', 'fake text', 'thought bubble',
    'multiple views', 'silent comic', 'out of frame censoring',
    'symbol only commentary', 'character watermark', 'spoken question mark',
    'japanese text', 'spanish text', 'language text', 'fanbox username',
    'ai generated', 'stable diffusion', 'tagme (artist)', 'text bubble',
    'chinese commentary', 'korean text', 'partial commentary', 'chinese text',
    'copyright request', 'heart censor', 'censored nipples', 'page number',
    'fake magazine cover', 'korean commentary',
    'sample watermark', 'copyright notice', 'copyright name', 'album cover', 'company name',
]

# ─── Tag cache manager ───────────────────────────────────────────────────────
try:
    from .cache_db import TagCacheManager
except ImportError:
    import sys
    _ranbooru_scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if _ranbooru_scripts_dir not in sys.path:
        sys.path.insert(0, _ranbooru_scripts_dir)
    from cache_db import TagCacheManager


tag_cache_manager = TagCacheManager(user_cache_dir)

# Initialize credentials manager
class CredentialsManager:
    def __init__(self, extension_root):
        self.extension_root = extension_root
        self.credentials_dir = os.path.join(extension_root, 'user', 'credentials')
        self.credentials_file = os.path.join(self.credentials_dir, 'credentials.json')
        
        # Create credentials directory if it doesn't exist
        os.makedirs(self.credentials_dir, exist_ok=True)
        
        # Initialize credentials file if it doesn't exist
        if not os.path.exists(self.credentials_file):
            self._save_credentials({})
    
    def _load_credentials(self):
        """Load credentials from the JSON file"""
        try:
            with open(self.credentials_file, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    
    def _save_credentials(self, credentials):
        """Save credentials to the JSON file"""
        with open(self.credentials_file, 'w') as f:
            json.dump(credentials, f, indent=2)
    
    def save_booru_credentials(self, booru_name, api_key, user_id=None):
        """Save API credentials for a specific booru"""
        credentials = self._load_credentials()
        
        if booru_name not in credentials:
            credentials[booru_name] = {}
        
        credentials[booru_name]['api_key'] = api_key
        if user_id is not None:
            credentials[booru_name]['user_id'] = user_id
        
        self._save_credentials(credentials)
    
    def get_booru_credentials(self, booru_name):
        """Get API credentials for a specific booru"""
        credentials = self._load_credentials()
        return credentials.get(booru_name, {})
    
    def has_credentials(self, booru_name):
        """Check if credentials exist for a specific booru"""
        credentials = self.get_booru_credentials(booru_name)
        return 'api_key' in credentials and credentials['api_key'].strip() != ''
    
    def clear_booru_credentials(self, booru_name):
        """Clear credentials for a specific booru"""
        credentials = self._load_credentials()
        if booru_name in credentials:
            del credentials[booru_name]
            self._save_credentials(credentials)

credentials_manager = CredentialsManager(extension_root)

if not os.path.isfile(os.path.join(user_search_dir, 'tags_search.txt')):
    with open(os.path.join(user_search_dir, 'tags_search.txt'), 'w'):
        pass
if not os.path.isfile(os.path.join(user_remove_dir, 'tags_remove.txt')):
    with open(os.path.join(user_remove_dir, 'tags_remove.txt'), 'w'):
        pass

COLORED_BG = ['black_background', 'aqua_background', 'white_background', 'colored_background', 'gray_background', 'blue_background', 'green_background', 'red_background', 'brown_background', 'purple_background', 'yellow_background', 'orange_background', 'pink_background', 'plain', 'transparent_background', 'simple_background', 'two-tone_background', 'grey_background']
ADD_BG = ['outdoors', 'indoors']
BW_BG = ['monochrome', 'greyscale', 'grayscale']
POST_AMOUNT = 100
COUNT = 100 #Number of images the search returned. Booru classes below were modified to update this value with the latest search result count.
DEBUG = False
RATING_TYPES = {
    "none": {
        "All": "All"
    },
    "full": {
        "All": "All",
        "Safe": "safe",
        "Questionable": "questionable",
        "Explicit": "explicit"
    },
    "single": {
        "All": "All",
        "Safe": "g",
        "Sensitive": "s",
        "Questionable": "q",
        "Explicit": "e"
    }
}
RATINGS = {
    "e621": RATING_TYPES['full'],
    "danbooru": RATING_TYPES['single'],
    "aibooru": RATING_TYPES['full'],
    "yande.re": RATING_TYPES['full'],
    "konachan": RATING_TYPES['full'],
    "safebooru": RATING_TYPES['none'],
    "rule34": RATING_TYPES['full'],
    "xbooru": RATING_TYPES['full'],
    "gelbooru": RATING_TYPES['single']
}


def get_available_ratings(booru):
    mature_ratings = gr.update(choices=list(RATINGS[booru].keys()), value="All")
    return mature_ratings


def show_fringe_benefits(booru):
    if booru == 'gelbooru':
        return gr.update(visible=True)
    else:
        return gr.update(visible=False)


def check_exception(booru, parameters):
    post_id = parameters.get('post_id')
    tags = parameters.get('tags')
    if booru == 'konachan' and post_id:
        raise Exception("Konachan does not support post IDs")
    if booru == 'yande.re' and post_id:
        raise Exception("Yande.re does not support post IDs")
    if booru == 'e621' and post_id:
        raise Exception("e621 does not support post IDs")


class Booru():

    def __init__(self, booru, booru_url):
        self.booru = booru
        self.base_url = booru_url
        self.booru_url = booru_url
        self.headers = {'user-agent': 'my-app/0.0.1'}

    def fetch_with_retry(self, url, max_retries=3, timeout=10, **kwargs):
        """Fetch URL with retry logic for rate limiting and network errors."""
        import time
        for attempt in range(max_retries):
            try:
                response = requests.get(url, timeout=timeout, **kwargs)
                if response.status_code == 429:
                    wait = 2 ** attempt
                    print(f"[{self.booru}] Rate limited (429), waiting {wait}s...")
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                return response
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    print(f"[{self.booru}] Timeout, retry {attempt + 1}/{max_retries}")
                    time.sleep(2 ** attempt)
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    print(f"[{self.booru}] Request error: {e}, retry {attempt + 1}/{max_retries}")
                    time.sleep(2 ** attempt)
        raise Exception(f"[{self.booru}] All {max_retries} attempts failed for {url}")

    def get_data(self, add_tags, max_pages=10, id=''):
        pass

    def get_post(self, add_tags, max_pages=10, id=''):
        return self.get_data(add_tags, max_pages, "&id=" + id)


class Gelbooru(Booru):

    def __init__(self, fringe_benefits=True, api_key=None, user_id=None):
        super().__init__('gelbooru', f'https://gelbooru.com/index.php?page=dapi&s=post&q=index&json=1&limit={POST_AMOUNT}')
        self.fringe_benefits = fringe_benefits
        self.api_key = api_key
        self.user_id = user_id

    def get_data(self, add_tags, max_pages=10, id=''):
        global COUNT
        loop_msg = True
        for _ in range(2):
            local_add_tags = '' if id else add_tags
            url = f"{self.base_url}&pid={random.randint(0, max_pages-1)}{id}{local_add_tags}"
            if self.api_key and self.user_id:
                url += f"&api_key={self.api_key}&user_id={self.user_id}"
            if self.fringe_benefits:
                url += "&fringeBenefits=1"
            self.booru_url = url
            res = self.fetch_with_retry(url, timeout=10)
            try:
                data = res.json()
            except Exception as e:
                print(f"[Gelbooru] JSON parse error: {e}")
                data = []
            if not isinstance(data, list):
                data = []
            COUNT = len(data)
            if COUNT == 0:
                max_pages = 2
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                continue
            break
        for post in data:
            if isinstance(post, dict) and 'directory' in post and 'image' in post:
                post['file_url'] = f"https://img3.gelbooru.com/images/{post['directory']}/{post['image']}"
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id=''):
        global COUNT
        local_add_tags = '' if id else add_tags
        url = f"{self.base_url}&pid={page}{id}{local_add_tags}"
        if self.api_key and self.user_id:
            url += f"&api_key={self.api_key}&user_id={self.user_id}"
        if self.fringe_benefits:
            url += "&fringeBenefits=1"
        self.booru_url = url
        res = self.fetch_with_retry(url, timeout=10)
        try:
            data = res.json()
        except Exception as e:
            print(f"[Gelbooru] JSON parse error (page): {e}")
            data = []
        if not isinstance(data, list):
            data = []
        COUNT = len(data)
        for post in data:
            if isinstance(post, dict) and 'directory' in post and 'image' in post:
                post['file_url'] = f"https://img3.gelbooru.com/images/{post['directory']}/{post['image']}"
        return {'post': data}

    def get_post(self, add_tags, max_pages=10, id=''):
        return self.get_data(add_tags, max_pages, "&id=" + id)


class e621(Booru):

    def __init__(self):
        super().__init__('e621', f'https://e621.net/posts.json?limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id='', tag_categories=None):
        global COUNT
        loop_msg = True
        for loop in range(2):
            if id:
                add_tags = ''
            random_page = random.randint(1, max(1, int(max_pages)))
            url = f"{self.base_url}&page={random_page}{add_tags}"
            self.booru_url = url
            res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
            data = res.json()
            posts = data.get('posts', []) if isinstance(data, dict) else []
            for post in posts:
                if isinstance(post, dict):
                    post['tags'] = self._filter_tags_by_category(post, tag_categories)
            COUNT = len(posts)
            if COUNT == 0:
                max_pages = 2
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                continue
            else:
                print(f"Found enough results")
            break
        return {'post': posts}

    def get_data_page(self, add_tags, page=0, id='', tag_categories=None):
        global COUNT
        if id:
            add_tags = ''
        safe_page = max(1, int(page) + 1)
        url = f"{self.base_url}&page={safe_page}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
        data = res.json()
        posts = data.get('posts', []) if isinstance(data, dict) else []
        for post in posts:
            if isinstance(post, dict):
                post['tags'] = self._filter_tags_by_category(post, tag_categories)
        COUNT = len(posts)
        return {'post': posts}

    def _filter_tags_by_category(self, post, categories):
        """Filter tags by selected categories (e621 uses nested tags dict)."""
        if not categories or not isinstance(categories, list):
            tags_dict = post.get('tags', {})
            if isinstance(tags_dict, dict):
                all_tags = []
                for cat in ['general', 'artist', 'copyright', 'character', 'species', 'meta']:
                    all_tags.extend(tags_dict.get(cat, []))
                return ' '.join(all_tags)
            return ''
        tags_dict = post.get('tags', {})
        if not isinstance(tags_dict, dict):
            return ''
        parts = []
        for cat in categories:
            if cat in tags_dict:
                parts.extend(tags_dict[cat])
        return ' '.join(parts) if parts else ''

    def get_post(self, add_tags, max_pages=10, id=''):
        if not id:
            return self.get_data(add_tags, max_pages, '', tag_categories=None)
        self.booru_url = f"https://danbooru.donmai.us/posts/{id}.json"
        res = self.fetch_with_retry(self.booru_url, headers=self.headers, timeout=10)
        data = res.json()
        if isinstance(data, dict):
            data['tags'] = data.get('tag_string', '')
            return {'post': [data]}
        return {'post': []}



class XBooru(Booru):

    def __init__(self):
        super().__init__('xbooru', f'https://xbooru.com/index.php?page=dapi&s=post&q=index&json=1&limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id=''):
        global COUNT
        loop_msg = True # avoid showing same msg twice
        for loop in range(2): # run loop at most twice
            if id:
                add_tags = ''
            url = f"{self.base_url}&pid={random.randint(0, max_pages-1)}{id}{add_tags}"
            self.booru_url = url
            print(url)
            res = self.fetch_with_retry(url, timeout=10)
            data = res.json()
            COUNT = 0
            for post in data:
                post['file_url'] = f"https://xbooru.com/images/{post['directory']}/{post['image']}"
                COUNT += 1
            if COUNT <= max_pages*POST_AMOUNT:
                max_pages = COUNT // POST_AMOUNT+1
                # If max_pages is bigger than available pages, loop the function with updated max_pages based on the value of COUNT
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                    # avoid showing same msg twice
                continue
            else:
                print(f" Processing {max_pages*POST_AMOUNT} out of {COUNT} results.")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id=''):
        global COUNT
        if id:
            add_tags = ''
        url = f"{self.base_url}&pid={page}{id}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url, timeout=10)
        data = res.json()
        COUNT = 0
        for post in data:
            post['file_url'] = f"https://xbooru.com/images/{post['directory']}/{post['image']}"
            COUNT += 1
        return {'post': data}

    def get_post(self, add_tags, max_pages=10, id=''):
        return self.get_data(add_tags, max_pages, "&id=" + id)


class Rule34(Booru):

    def __init__(self, api_key=None, user_id=None):
        super().__init__('rule34', f'https://api.rule34.xxx/index.php?page=dapi&s=post&q=index&json=1&limit={POST_AMOUNT}')
        self.api_key = api_key
        self.user_id = user_id

    def get_data(self, add_tags, max_pages=10, id=''):
        global COUNT
        loop_msg = True # avoid showing same msg twice
        for loop in range(2): # run loop at most twice
            if id:
                add_tags = ''
            url = f"{self.base_url}&pid={random.randint(0, max_pages-1)}{id}{add_tags}"
            if self.api_key and self.user_id:
                url += f"&api_key={self.api_key}&user_id={self.user_id}"
            self.booru_url = url
            res = self.fetch_with_retry(url, timeout=10)
            try:
                data = res.json()
            except Exception as e:
                print(f"[Rule34] JSON parse error: {e}")
                data = []
            if not isinstance(data, list):
                data = []
            COUNT = len(data)
            if COUNT == 0:
                max_pages = 2
                # Rule34 does not have a way to know the amount of results available in the search, so we need to run the function again with a fixed amount of pages
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                    # avoid showing same msg twice
                continue
            else:
                print(f"Found enough results")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id=''):
        global COUNT
        if id:
            add_tags = ''
        url = f"{self.base_url}&pid={page}{id}{add_tags}"
        if self.api_key and self.user_id:
            url += f"&api_key={self.api_key}&user_id={self.user_id}"
        self.booru_url = url
        res = self.fetch_with_retry(url, timeout=10)
        try:
            data = res.json()
        except Exception as e:
            print(f"[Rule34] JSON parse error (page): {e}")
            data = []
        if not isinstance(data, list):
            data = []
        COUNT = len(data)
        return {'post': data}

    def get_post(self, add_tags, max_pages=10, id=''):
        return self.get_data(add_tags, max_pages, "&id=" + id)


class Safebooru(Booru):

    def __init__(self):
        super().__init__('safebooru', f'https://safebooru.donmai.us/posts.json?limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id='', tag_categories=None):
        global COUNT
        loop_msg = True
        for loop in range(2):
            if id:
                add_tags = ''
            random_page = random.randint(1, max(1, int(max_pages)))
            url = f"{self.base_url}&page={random_page}{add_tags}"
            self.booru_url = url
            res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
            data = res.json()
            if not isinstance(data, list):
                try:
                    data = data.get('posts', [])
                except AttributeError:
                    data = []
            COUNT = 0
            for post in data:
                if isinstance(post, dict):
                    post['tags'] = self._filter_tags_by_category(post, tag_categories)
                    if not post.get('file_url') and post.get('large_file_url'):
                        post['file_url'] = post.get('large_file_url')
                    COUNT += 1
            if COUNT == 0:
                max_pages = 2
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                continue
            else:
                print(f"Found enough results")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id='', tag_categories=None):
        global COUNT
        if id:
            add_tags = ''
        safe_page = max(1, int(page) + 1)
        url = f"{self.base_url}&page={safe_page}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
        data = res.json()
        if not isinstance(data, list):
            try:
                data = data.get('posts', [])
            except AttributeError:
                data = []
        COUNT = 0
        for post in data:
            if isinstance(post, dict):
                post['tags'] = self._filter_tags_by_category(post, tag_categories)
                if not post.get('file_url') and post.get('large_file_url'):
                    post['file_url'] = post.get('large_file_url')
                COUNT += 1
        return {'post': data}

    def _filter_tags_by_category(self, post, categories):
        """Filter tags by selected categories."""
        if not categories or not isinstance(categories, list):
            return post.get('tag_string', '')
        parts = []
        for cat in categories:
            field = f'tag_string_{cat}'
            if field in post:
                parts.append(post[field])
        return ' '.join(parts) if parts else post.get('tag_string', '')

    def get_post(self, add_tags, max_pages=10, id=''):
        if not id:
            return self.get_data(add_tags, max_pages, '', tag_categories=None)
        self.booru_url = f"https://safebooru.donmai.us/posts/{id}.json"
        res = self.fetch_with_retry(self.booru_url, headers=self.headers, timeout=10)
        data = res.json()
        if isinstance(data, dict):
            data['tags'] = data.get('tag_string', '')
            if not data.get('file_url') and data.get('large_file_url'):
                data['file_url'] = data.get('large_file_url')
            return {'post': [data]}
        return {'post': []}


class Konachan(Booru):

    def __init__(self):
        super().__init__('konachan', f'https://konachan.com/post.json?limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id=''):
        global COUNT
        loop_msg = True # avoid showing same msg twice
        for loop in range(2): # run loop at most twice
            if id:
                add_tags = ''
            url = f"{self.base_url}&page={random.randint(0, max_pages-1)}{id}{add_tags}"
            self.booru_url = url
            res = self.fetch_with_retry(url, timeout=10)
            if res.status_code != 200:
                data = []
            else:
                try:
                    data = res.json()
                except Exception as e:
                    print(f"[Konachan] JSON parse error: {e}")
                    data = []
            COUNT = len(data)
            if COUNT == 0:
                max_pages = 2
                # Konachan does not have a way to know the amount of results available in the search, so we need to run the function again with a fixed amount of pages
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                    # avoid showing same msg twice
                continue
            else:
                print(f"Found enough results")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id=''):
        global COUNT
        if id:
            add_tags = ''
        url = f"{self.base_url}&page={page}{id}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url, timeout=10)
        if res.status_code != 200:
            data = []
        else:
            try:
                data = res.json()
            except Exception as e:
                print(f"[Konachan] JSON parse error (page): {e}")
                data = []
        COUNT = len(data)
        return {'post': data}

    def get_post(self, add_tags, max_pages=10, id=''):
        raise Exception("Konachan does not support post IDs")


class Yandere(Booru):

    def __init__(self):
        super().__init__('yande.re', f'https://yande.re/post.json?api_version=2')

    def get_data(self, add_tags, max_pages=10, id=''):
        global COUNT
        loop_msg = True # avoid showing same msg twice
        for loop in range(2): # run loop at most twice
            if id:
                add_tags = ''
            page = random.randint(0, max_pages-1)
            extras = '&filter=1&include_tags=1&include_votes=1&include_pools=1'
            url = f"{self.base_url}&limit={POST_AMOUNT}&page={page}{id}{add_tags}{extras}"
            self.booru_url = url
            res = self.fetch_with_retry(url, timeout=10)
            posts = []
            if res.status_code == 200:
                try:
                    data = res.json()
                    if isinstance(data, dict):
                        posts = data.get('posts', [])
                    elif isinstance(data, list):
                        posts = data
                except Exception as e:
                    print(f"[Yandere] JSON parse error: {e}")
                    posts = []
            COUNT = len(posts)
            if COUNT == 0:
                max_pages = 2
                # Yandere does not have a way to know the amount of results available in the search, so we need to run the function again with a fixed amount of pages
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                    # avoid showing same msg twice
                continue
            else:
                print(f"Found enough results")
            break
        return {'post': posts}

    def get_data_page(self, add_tags, page=0, id=''):
        global COUNT
        if id:
            add_tags = ''
        extras = '&filter=1&include_tags=1&include_votes=1&include_pools=1'
        url = f"{self.base_url}&limit={POST_AMOUNT}&page={page}{id}{add_tags}{extras}"
        self.booru_url = url
        res = self.fetch_with_retry(url, timeout=10)
        posts = []
        if res.status_code == 200:
            try:
                data = res.json()
                if isinstance(data, dict):
                    posts = data.get('posts', [])
                elif isinstance(data, list):
                    posts = data
            except Exception:
                posts = []
        COUNT = len(posts)
        return {'post': posts}

    def get_post(self, add_tags, max_pages=10, id=''):
        raise Exception("Yande.re does not support post IDs")


class AIBooru(Booru):

    def __init__(self):
        super().__init__('AIBooru', f'https://aibooru.online/posts.json?limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id='', tag_categories=None):
        global COUNT
        loop_msg = True
        for loop in range(2):
            if id:
                add_tags = ''
            url = f"{self.base_url}&page={random.randint(0, max_pages-1)}{id}{add_tags}"
            self.booru_url = url
            res = self.fetch_with_retry(url)
            data = res.json()
            for post in data:
                post['tags'] = self._filter_tags_by_category(post, tag_categories)
            COUNT = len(data)
            if COUNT == 0:
                max_pages = 2
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                continue
            else:
                print(f"Found enough results")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id='', tag_categories=None):
        global COUNT
        if id:
            add_tags = ''
        url = f"{self.base_url}&page={page}{id}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url)
        data = res.json()
        for post in data:
            post['tags'] = self._filter_tags_by_category(post, tag_categories)
        COUNT = len(data)
        return {'post': data}

    def _filter_tags_by_category(self, post, categories):
        """Filter tags by selected categories."""
        if not categories or not isinstance(categories, list):
            return post.get('tag_string', '')
        parts = []
        for cat in categories:
            field = f'tag_string_{cat}'
            if field in post:
                parts.append(post[field])
        return ' '.join(parts) if parts else post.get('tag_string', '')

    def get_post(self, add_tags, max_pages=10, id=''):
        raise Exception("AIBooru does not support post IDs")


class Danbooru(Booru):

    def __init__(self):
        super().__init__('danbooru', f'https://danbooru.donmai.us/posts.json?limit={POST_AMOUNT}')

    def get_data(self, add_tags, max_pages=10, id='', tag_categories=None):
        global COUNT
        loop_msg = True
        for loop in range(2):
            if id:
                add_tags = ''
            random_page = random.randint(1, max(1, int(max_pages)))
            url = f"{self.base_url}&page={random_page}{add_tags}"
            self.booru_url = url
            res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
            data = res.json()
            if not isinstance(data, list):
                try:
                    data = data.get('posts', [])
                except AttributeError:
                    data = []
            for post in data:
                if isinstance(post, dict):
                    post['tags'] = self._filter_tags_by_category(post, tag_categories)
            COUNT = len(data)
            if COUNT == 0:
                max_pages = 2
                while loop_msg:
                    print(f" Processing {COUNT} results.")
                    loop_msg = False
                continue
            else:
                print(f"Found enough results")
            break
        return {'post': data}

    def get_data_page(self, add_tags, page=0, id='', tag_categories=None):
        global COUNT
        if id:
            add_tags = ''
        safe_page = max(1, int(page) + 1)
        url = f"{self.base_url}&page={safe_page}{add_tags}"
        self.booru_url = url
        res = self.fetch_with_retry(url, headers=self.headers, timeout=10)
        data = res.json()
        if not isinstance(data, list):
            try:
                data = data.get('posts', [])
            except AttributeError:
                data = []
        for post in data:
            if isinstance(post, dict):
                post['tags'] = self._filter_tags_by_category(post, tag_categories)
        COUNT = len(data)
        return {'post': data}

    def _filter_tags_by_category(self, post, categories):
        """Filter tags by selected categories."""
        if not categories or not isinstance(categories, list):
            return post.get('tag_string', '')
        parts = []
        for cat in categories:
            field = f'tag_string_{cat}'
            if field in post:
                parts.append(post[field])
        return ' '.join(parts) if parts else post.get('tag_string', '')

    def get_post(self, add_tags, max_pages=10, id=''):
        if not id:
            return self.get_data(add_tags, max_pages, '', tag_categories=None)
        self.booru_url = f"https://danbooru.donmai.us/posts/{id}.json"
        res = self.fetch_with_retry(self.booru_url, headers=self.headers, timeout=10)
        data = res.json()
        if isinstance(data, dict):
            data['tags'] = data.get('tag_string', '')
            return {'post': [data]}
        return {'post': []}


def generate_chaos(pos_tags, neg_tags, chaos_amount):
    """Generates chaos in the prompt by adding random tags from the prompt to the positive and negative prompts

    Args:
        pos_tags (str): the positive prompt
        neg_tags (str): the negative prompt
        chaos_amount (float): the percentage of tags to put in the positive prompt

    Returns:
        str: the positive prompt
        str: the negative prompt
    """
    # create a list with the tags in the prompt and in the negative prompt
    chaos_list = [tag for tag in pos_tags.split(',') + neg_tags.split(',') if tag.strip() != '']
    # distinct the list
    chaos_list = list(set(chaos_list))
    random.shuffle(chaos_list)
    # put 50% of the tags in the prompt and the remaining 50% in the negative prompt
    len_list = round(len(chaos_list) * chaos_amount)
    pos_list = chaos_list[len_list:]
    pos_prompt = ','.join(pos_list)
    neg_list = chaos_list[:len_list]
    random.shuffle(neg_list)
    neg_prompt = ','.join(neg_list)
    return pos_prompt, neg_prompt


def resize_image(img, width, height, cropping=True):
    """Resize image to specified width and height

    Args:
        img (PIL.Image): the image
        width (int): the width in pixels
        height (int): the height in pixels
        cropping (bool): whether to crop the image or not

    Returns:
        PIL.Image: the resized image
    """
    if cropping:
        # resize the picture and center crop it
        # example: you have a 100x200 picture and width=300 and height=300
        # resize to 300x600 and crop to 300x300 from the center
        x, y = img.size
        if x < y:
            # scale to width keeping aspect ratio
            wpercent = (width / float(img.size[0]))
            hsize = int((float(img.size[1]) * float(wpercent)))
            img_new = img.resize((width, hsize))
            if img_new.size[1] < height:
                # scale to height keeping aspect ratio
                hpercent = (height / float(img.size[1]))
                wsize = int((float(img.size[0]) * float(hpercent)))
                img_new = img.resize((wsize, height))
        else:
            ypercent = (height / float(img.size[1]))
            wsize = int((float(img.size[0]) * float(ypercent)))
            img_new = img.resize((wsize, height))
            if img_new.size[0] < width:
                xpercent = (width / float(img.size[0]))
                hsize = int((float(img.size[1]) * float(xpercent)))
                img_new = img.resize((width, hsize))

        # crop center
        x, y = img_new.size
        left = (x - width) / 2
        top = (y - height) / 2
        right = (x + width) / 2
        bottom = (y + height) / 2
        img = img_new.crop((left, top, right, bottom))
    else:
        img = img.resize((width, height))
    return img

def modify_prompt(prompt, tagged_prompt, type_deepbooru):
    """Modifies the prompt based on the type_deepbooru selected

    Args:
        prompt (str): the prompt
        tagged_prompt (str): the prompt tagged by deepbooru
        type_deepbooru (str): the type of modification

    Returns:
        str: the modified prompt
    """
    if type_deepbooru == 'Add Before':
        return tagged_prompt + ',' + prompt
    elif type_deepbooru == 'Add After':
        return prompt + ',' + tagged_prompt
    elif type_deepbooru == 'Replace':
        return tagged_prompt
    return prompt

def remove_repeated_tags(prompt):
    """Removes the repeated tags keeping the same order

    Args:
        prompt (str): the prompt

    Returns:
        str: the prompt without repeated tags
    """
    prompt = prompt.split(',')
    new_prompt = []
    for tag in prompt:
        if tag not in new_prompt:
            new_prompt.append(tag)
    return ','.join(new_prompt)

def limit_prompt_tags(prompt, limit_tags, mode):
    """Limits the amount of tags in the prompt. It can be done by percentage or by a fixed amount.

    Args:
        prompt (str): the prompt
        limit_tags (float): the percentage of tags to keep
        mode (str): 'Limit' or 'Max'

    Returns:
        str: the prompt with the limited amount of tags
    """
    clean_prompt = prompt.split(',')
    if mode == 'Limit':
        clean_prompt = clean_prompt[:int(len(clean_prompt) * limit_tags)]
    elif mode == 'Max':
        clean_prompt = clean_prompt[:int(limit_tags)]
    return ','.join(clean_prompt)


# ─── 批量爬取辅助函数 ─────────────────────────────────────────────────────────
def batch_fetch_tags(booru_name, tags_search, max_pages, fringe_benefits,
                     remove_bad_tags, remove_tags_str, shuffle_tags, change_dash,
                     limit_tags, max_tags, mature_rating,
                     api_key='', user_id_str='', save_credentials=False,
                     use_remove_txt=False, choose_remove_txt='', tag_categories=None):
    """从 booru 批量抓取多页帖子的 tags，返回 cleaned tag 字符串列表"""
    max_pages = int(max_pages)

    gelbooru_api_key = None
    gelbooru_user_id = None
    rule34_api_key = None
    rule34_user_id = None
    if booru_name == 'gelbooru':
        if api_key.strip() and user_id_str.strip():
            gelbooru_api_key = api_key.strip()
            gelbooru_user_id = user_id_str.strip()
            if save_credentials:
                credentials_manager.save_booru_credentials('gelbooru', gelbooru_api_key, gelbooru_user_id)
        else:
            saved = credentials_manager.get_booru_credentials('gelbooru')
            gelbooru_api_key = saved.get('api_key', '')
            gelbooru_user_id = saved.get('user_id', '')
    if booru_name == 'rule34':
        if api_key.strip() and user_id_str.strip():
            rule34_api_key = api_key.strip()
            rule34_user_id = user_id_str.strip()
            if save_credentials:
                credentials_manager.save_booru_credentials('rule34', rule34_api_key, rule34_user_id)
        else:
            saved = credentials_manager.get_booru_credentials('rule34')
            rule34_api_key = saved.get('api_key', '')
            rule34_user_id = saved.get('user_id', '')

    booru_apis = {
        'gelbooru': Gelbooru(fringe_benefits, gelbooru_api_key, gelbooru_user_id),
        'rule34': Rule34(rule34_api_key, rule34_user_id),
        'safebooru': Safebooru(),
        'danbooru': Danbooru(),
        'konachan': Konachan(),
        'yande.re': Yandere(),
        'aibooru': AIBooru(),
        'xbooru': XBooru(),
        'e621': e621(),
    }

    api = booru_apis.get(booru_name, Gelbooru(fringe_benefits, gelbooru_api_key, gelbooru_user_id))

    add_tags = '&tags=-animated'
    if tags_search:
        add_tags += '+' + tags_search.replace(',', '+')
        if mature_rating != 'All' and booru_name in RATINGS:
            rating_val = RATINGS[booru_name].get(mature_rating)
            if rating_val and rating_val != 'All':
                add_tags += f'+rating:{rating_val}'

    # Build bad_tags
    bad_tags = []
    if remove_bad_tags:
        bad_tags = list(DEFAULT_BAD_TAGS)
    if remove_tags_str:
        if ',' in remove_tags_str:
            bad_tags.extend(remove_tags_str.split(','))
        else:
            bad_tags.append(remove_tags_str)
    if use_remove_txt and choose_remove_txt:
        try:
            bad_tags.extend(open(os.path.join(user_remove_dir, choose_remove_txt), 'r').read().split(','))
        except Exception:
            pass

    all_tags = []
    for page in range(max_pages):
        try:
            if hasattr(api, 'get_data_page'):
                if booru_name in ['danbooru', 'safebooru', 'aibooru', 'e621'] and tag_categories:
                    data = api.get_data_page(add_tags, page, '', tag_categories)
                else:
                    data = api.get_data_page(add_tags, page)
            else:
                data = api.get_data(add_tags, max_pages=1)
            posts = data.get('post', [])
            if not isinstance(posts, list):
                posts = []
            if len(posts) == 0:
                print(f"[TagCache] 第 {page+1} 页无数据，停止抓取")
                break
            for post in posts:
                if not isinstance(post, dict):
                    continue
                raw_tags = post.get('tags', '')
                if not raw_tags:
                    continue
                # Split and filter bad tags first
                tag_list = raw_tags.split(' ')
                if shuffle_tags:
                    random.shuffle(tag_list)
                tag_list = [t for t in tag_list if t.strip() not in bad_tags]
                for bt in bad_tags:
                    if '*' in bt:
                        tag_list = [t for t in tag_list if bt.replace('*', '') not in t]
                # Join with commas
                prompt_str = ','.join(tag_list)
                if change_dash:
                    prompt_str = prompt_str.replace('_', ' ')
                if limit_tags < 1:
                    prompt_str = limit_prompt_tags(prompt_str, limit_tags, 'Limit')
                if max_tags > 0:
                    prompt_str = limit_prompt_tags(prompt_str, int(max_tags), 'Max')
                if prompt_str.strip():
                    all_tags.append(prompt_str)
            print(f"[TagCache] 第 {page+1}/{max_pages} 页完成，获取 {len(posts)} 条")
        except Exception as ex:
            print(f"[TagCache] 第 {page+1} 页出错: {ex}")
            break

    return all_tags


def _is_hires_second_pass(p):
    """
    尽可能识别 Hires.fix 第二段。
    """
    # A1111 常见标记
    if getattr(p, "is_hr_pass", False):
        return True
    # Forge/部分分支第二段常是 Img2Img + 有 init_images
    init_images = getattr(p, "init_images", None)
    if isinstance(p, StableDiffusionProcessingImg2Img) and init_images:
        # 如果是普通 img2img（不是 hires）也可能有 init_images，所以再加一点条件
        # 第二段通常还会有 denoising_strength / hr 相关字段
        if hasattr(p, "denoising_strength") or getattr(p, "enable_hr", False):
            return True
    return False


class Script(scripts.Script):
    def __init__(self):
        super().__init__()
        self.prompt_area = [None, None]
        self.prompt_row = [None, None]
        self.input_row = [None, None]
        self.action_row = [None, None]
        self.on_after_component_elem_id = [
            ("txt2img_prompt_row", lambda x: self.create_prompt_row(0, x)),
            ("img2img_prompt_row", lambda x: self.create_prompt_row(1, x)),
            ("txt2img_prompt", lambda x: self.set_prompt_area(0, x)),
            ("img2img_prompt", lambda x: self.set_prompt_area(1, x)),
        ]
        self._local_cache_applied_jobs = set()   # 新增：记录已注入缓存的任务

    def create_prompt_row(self, i2i, component):
        self.prompt_row[i2i] = gr.Row()
        self.input_row[i2i] = gr.Row()
        self.action_row[i2i] = gr.Row()

    def set_prompt_area(self, i2i, component):
        try:
            self.prompt_area[i2i] = component.component if hasattr(component, "component") else component
        except Exception:
            self.prompt_area[i2i] = None
    previous_loras = ''
    last_img = []
    real_steps = 0
    version = "1.3"
    original_prompt = ''

    def get_files(self, path):
        files = []
        for file in os.listdir(path):
            if file.endswith('.txt'):
                files.append(file)
        return files

    def hide_object(self, obj, booru):
        print(f'hide_object: {obj}, {booru.value}')
        if booru.value == 'konachan' or booru.value == 'yande.re':
            obj.interactive = False
        else:
            obj.interactive = True

    def title(self):
        return "Ranbooru"

    def show(self, is_img2img):
        return scripts.AlwaysVisible

    def show_gelbooru_api_fields(self, booru):
        """Show/hide API key fields based on selected booru"""
        if booru in ['gelbooru', 'rule34']:
            return gr.update(visible=True)
        else:
            return gr.update(visible=False)
    
    def load_gelbooru_credentials(self, booru):
        """Load saved credentials for selected booru"""
        if booru in ['gelbooru', 'rule34']:
            credentials = credentials_manager.get_booru_credentials(booru)
            api_key = credentials.get('api_key', '')
            user_id = credentials.get('user_id', '')
            has_saved_creds = credentials_manager.has_credentials(booru)
            
            if has_saved_creds:
                # Hide input fields and show file path
                credentials_file_path = credentials_manager.credentials_file
                status_text = f"✓ Credentials loaded from: {credentials_file_path}"
                return (
                    gr.update(visible=False),  # api_key field
                    gr.update(visible=False),  # user_id field  
                    gr.update(visible=True, value=status_text),  # credentials_status
                    gr.update(visible=True, value="Clear saved credentials")  # clear_credentials_btn
                )
            else:
                # Show input fields
                return (
                    gr.update(visible=True, value=api_key),  # api_key field
                    gr.update(visible=True, value=user_id),  # user_id field
                    gr.update(visible=False, value=""),  # credentials_status
                    gr.update(visible=False)  # clear_credentials_btn
                )
        else:
            return (
                gr.update(visible=False, value=""),  # api_key field
                gr.update(visible=False, value=""),  # user_id field
                gr.update(visible=False, value=""),  # credentials_status
                gr.update(visible=False)  # clear_credentials_btn
            )
    
    def clear_gelbooru_credentials(self, booru="gelbooru"):
        credentials_manager.clear_booru_credentials(booru)
        return (
            gr.update(visible=True, value=""),
            gr.update(visible=True, value=""),
            gr.update(visible=False, value=""),
            gr.update(visible=False)
        )

    def save_gelbooru_credentials(self, booru, api_key, user_id, save_credentials):
        """Save Gelbooru credentials if checkbox is checked"""
        if booru == 'gelbooru' and save_credentials and api_key.strip() and user_id.strip():
            credentials_manager.save_booru_credentials('gelbooru', api_key.strip(), user_id.strip())
            return "✓ Credentials saved"
        elif booru == 'gelbooru' and not save_credentials:
            # Optionally clear credentials if save is unchecked
            return "Credentials will not be saved"
        return ""

    def refresh_ser(self):
        return gr.update(choices=self.get_files(user_search_dir))
    def refresh_rem(self):
        return gr.update(choices=self.get_files(user_remove_dir))

    # ─── Tag Cache 面板回调 ───────────────────────────────────────────────
    @staticmethod
    def _cache_batch_fetch(booru, tags_search, max_pages, fringe_benefits,
                           remove_bad_tags, remove_tags_str, shuffle_tags,
                           change_dash, limit_tags, max_tags, mature_rating,
                           api_key, user_id_str, save_credentials,
                           use_remove_txt, choose_remove_txt, append_mode, tag_categories):
        """批量爬取并保存到缓存"""
        try:
            tags_list = batch_fetch_tags(
                booru, tags_search, max_pages, fringe_benefits,
                remove_bad_tags, remove_tags_str, shuffle_tags, change_dash,
                limit_tags, max_tags, mature_rating,
                api_key, user_id_str, save_credentials,
                use_remove_txt, choose_remove_txt, tag_categories
            )
            if not tags_list:
                return "未抓取到任何 tag 数据", tag_cache_manager.get_status()
            if append_mode:
                total = tag_cache_manager.append_cache(tags_list)
                msg = f"✅ 追加完成！本次新增 {len(tags_list)} 条，缓存总计 {total} 条"
            else:
                tag_cache_manager.save_cache(tags_list)
                tag_cache_manager.reset_index()
                msg = f"✅ 覆盖保存完成！共 {len(tags_list)} 条（索引已重置）"
            return msg, tag_cache_manager.get_status()
        except Exception as ex:
            return f"❌ 错误: {ex}", tag_cache_manager.get_status()

    @staticmethod
    def _cache_get_next(loop_mode):
        """从缓存顺序取出下一条"""
        tags, idx, total = tag_cache_manager.get_next_tags_from_pool(loop=loop_mode)
        if tags is None:
            if total == 0:
                return "缓存为空，请先批量爬取", tag_cache_manager.get_status()
            else:
                return f"已到达缓存末尾 (索引 {idx}/{total})", tag_cache_manager.get_status()
        return tags, tag_cache_manager.get_status()

    @staticmethod
    def _cache_reset_index():
        tag_cache_manager.reset_index()
        return "✅ 索引已重置为 0", tag_cache_manager.get_status()

    @staticmethod
    def _cache_delete():
        tag_cache_manager.delete_cache()
        return "✅ 缓存文件已删除", tag_cache_manager.get_status()

    @staticmethod
    def _cache_search(keyword):
        """搜索缓存"""
        results = tag_cache_manager.search_cache(keyword)
        return results if results else []

    @staticmethod
    def _cache_fill_by_id(tag_id):
        """根据ID填充标签"""
        tags = tag_cache_manager.get_by_id(int(tag_id))
        if tags:
            return tags, f"✅ 已填充 ID {int(tag_id)}"
        return "", f"❌ 未找到 ID {int(tag_id)}"

    @staticmethod
    def _cache_delete_by_id(tag_id):
        """根据ID删除标签"""
        success = tag_cache_manager.delete_by_id(int(tag_id))
        if success:
            return f"✅ 已删除 ID {int(tag_id)}", tag_cache_manager.get_status()
        return f"❌ 删除失败", tag_cache_manager.get_status()

    @staticmethod
    def _cache_refresh_status():
        pool_status = tag_cache_manager.get_filtered_pool_status()
        using_pool = tag_cache_manager.is_using_filtered_pool()
        status_msg = f"{tag_cache_manager.get_status()} | {pool_status}"
        if using_pool:
            status_msg += " [当前使用筛选池]"
        return status_msg

    @staticmethod
    def _cache_get_next_and_set(loop_mode, tag_prompt_text, current_prompt):
        """从缓存顺序取出下一条并设置到提示词"""
        tags, idx, total = tag_cache_manager.get_next_tags_from_pool(loop=loop_mode)
        if tags is None:
            if total == 0:
                return "缓存为空，请先批量爬取", tag_cache_manager.get_status(), current_prompt
            else:
                return f"已到达缓存末尾 (索引 {idx}/{total})", tag_cache_manager.get_status(), current_prompt
        if tag_prompt_text and tag_prompt_text.strip():
            combined = f"{tag_prompt_text.strip()},{tags}"
        else:
            combined = tags
        return tags, tag_cache_manager.get_status(), combined

    @staticmethod
    def _filter_tags(must_include, must_exclude):
        """筛选标签"""
        try:
            results = tag_cache_manager.filter_tags_by_keywords(must_include, must_exclude)
            if not results:
                return [], "未找到匹配的标签"
            return results, f"找到 {len(results)} 条匹配标签"
        except Exception as e:
            print(f"[Filter] Error: {e}")
            return [], f"筛选出错: {e}"

    @staticmethod
    def _create_filtered_pool(selected_ids_str):
        """创建筛选池"""
        try:
            if not selected_ids_str or not selected_ids_str.strip():
                return "请输入要添加的 ID（逗号分隔）", tag_cache_manager.get_filtered_pool_status()
            
            id_list = [int(x.strip()) for x in selected_ids_str.split(',') if x.strip().isdigit()]
            if not id_list:
                return "未找到有效的 ID", tag_cache_manager.get_filtered_pool_status()
            
            count = tag_cache_manager.create_filtered_pool(id_list)
            return f"✅ 筛选池已创建，包含 {count} 条标签", tag_cache_manager.get_filtered_pool_status()
        except Exception as e:
            print(f"[FilterPool] Error: {e}")
            return f"创建失败: {e}", tag_cache_manager.get_filtered_pool_status()

    @staticmethod
    def _switch_to_filtered_pool(use_pool):
        """切换到筛选池"""
        msg = tag_cache_manager.use_filtered_pool(use_pool)
        return msg, tag_cache_manager.get_filtered_pool_status()

    @staticmethod
    def _get_filtered_pool_status():
        """获取筛选池状态"""
        return tag_cache_manager.get_filtered_pool_status()

    def ui(self, is_img2img):
        default_booru = "safebooru"
        # Determine initial Gelbooru credential visibility based on saved credentials
        has_saved = credentials_manager.has_credentials('gelbooru')
        saved_creds = credentials_manager.get_booru_credentials('gelbooru') if has_saved else {}
        initial_api_key_visible = not has_saved
        initial_user_id_visible = not has_saved
        initial_status_visible = has_saved
        initial_status_value = f"✓ Credentials loaded from: {credentials_manager.credentials_file}" if has_saved else ""
        initial_clear_visible = has_saved
        
        row_container = self.prompt_row[is_img2img] or gr.Row()
        input_row = self.input_row[is_img2img] or gr.Row()
        action_row = self.action_row[is_img2img] or gr.Row()
        with row_container:
            with input_row:
                with gr.Column(scale=2, min_width=220):
                    tags = gr.Textbox(lines=1, label="Tags to Search (Pre)")
                with gr.Column(scale=8):
                    tag_prompt_input = gr.Textbox(lines=3, label="Tag Prompt")
            with action_row:
                with gr.Column(scale=2, min_width=220):
                    generate_prompt_btn = gr.Button("生成提示词")
                with gr.Column(scale=8):
                    with gr.Accordion(label="Ranbooru", open=False):
                        enabled = gr.Checkbox(label="Enabled", value=False)
                        with gr.Row():
                            with gr.Column(scale=1):
                                booru = gr.Dropdown(["safebooru", "rule34", "danbooru", "gelbooru", 'konachan', 'yande.re', 'aibooru', 'xbooru', 'e621'], label="Booru", value=default_booru)
                                tag_categories = gr.CheckboxGroup(
                                    ["general", "character", "copyright", "artist", "meta"],
                                    value=["general", "character", "copyright"],
                                    label="Tag Categories (Danbooru/Safebooru/AIBooru/e621)",
                                    visible=default_booru in ['danbooru', 'safebooru', 'aibooru', 'e621']
                                )
                                max_pages = gr.Number(label="Max Pages", minimum=1, maximum=9999, value=100, step=1, precision=0)
                                gr.Markdown("""## Post""")
                                post_id = gr.Textbox(lines=1, label="Post ID")
                                gr.Markdown("""## Tags""")
                                remove_tags = gr.Textbox(lines=1, label="Tags to Remove (Post)")
                                with gr.Group():
                                    with gr.Group():
                                        prompt_output = gr.Textbox(lines=3, label="提示词输出")
                            with gr.Column(scale=1):
                                mature_rating = gr.Radio(list(RATINGS[default_booru]), label="Mature Rating", value="All")
                                remove_bad_tags = gr.Checkbox(label="Remove bad tags", value=True)
                                shuffle_tags = gr.Checkbox(label="Shuffle tags", value=True)
                                change_dash = gr.Checkbox(label='Convert "_" to spaces', value=False)
                                same_prompt = gr.Checkbox(label="Use same prompt for all images", value=False)
                                fringe_benefits = gr.Checkbox(label="Fringe Benefits", value=(default_booru == 'gelbooru'), visible=(default_booru == 'gelbooru'))
                                with gr.Group(visible=False) as gelbooru_credentials_group:
                                    gr.Markdown("### API Credentials")
                                    api_key = gr.Textbox(
                                        lines=1, label="API Key", placeholder="Enter your API key",
                                        type="password", visible=initial_api_key_visible, value=saved_creds.get('api_key', '')
                                    )
                                    user_id = gr.Textbox(
                                        lines=1, label="User ID", placeholder="Enter your user ID",
                                        visible=initial_user_id_visible, value=saved_creds.get('user_id', '')
                                    )
                                    save_credentials = gr.Checkbox(label="Save credentials", value=False, visible=True)
                                    credentials_status = gr.Textbox(
                                        label="Status", interactive=False,
                                        visible=True, value=initial_status_value
                                    )
                                    clear_credentials_btn = gr.Button(
                                        "Clear saved credentials", visible=initial_clear_visible
                                    )
                                limit_tags = gr.Slider(value=1.0, label="Limit tags", minimum=0.05, maximum=1.0, step=0.05)
                                max_tags = gr.Slider(value=100, label="Max tags", minimum=1, maximum=100, step=1)
                                change_background = gr.Radio(["Don't Change", "Add Background", "Remove Background", "Remove All"], label="Change Background", value="Don't Change")
                                change_color = gr.Radio(["Don't Change", "Colored", "Limited Palette", "Monochrome"], label="Change Color", value="Don't Change")
                            sorting_order = gr.Radio(["Random", "High Score", "Low Score"], label="Sorting Order", value="Random")            
                        booru.change(get_available_ratings, booru, mature_rating)
                        booru.change(show_fringe_benefits, booru, fringe_benefits)
                        booru.change(self.show_gelbooru_api_fields, booru, gelbooru_credentials_group)
                        booru.change(
                            fn=lambda b: gr.update(visible=b in ['danbooru', 'safebooru', 'aibooru', 'e621']),
                            inputs=[booru],
                            outputs=[tag_categories]
                        )

                        with gr.Accordion("Img2Img", open=False):
                            use_img2img = gr.Checkbox(label="Use img2img", value=False)
                            use_ip = gr.Checkbox(label="Send to Controlnet", value=False)
                            denoising = gr.Slider(value=0.75, label="Denoising", minimum=0.05, maximum=1.0, step=0.05)
                            use_last_img = gr.Checkbox(label="Use last image as img2img", value=False)
                            crop_center = gr.Checkbox(label="Crop Center", value=False)
                            use_deepbooru = gr.Checkbox(label="Use Deepbooru", value=False)
                            type_deepbooru = gr.Radio(["Add Before", "Add After", "Replace"], label="Deepbooru Tags Position", value="Add Before")
                        with gr.Accordion("File", open=False):
                            use_search_txt = gr.Checkbox(label="Use tags_search.txt", value=False)
                            choose_search_txt = gr.Dropdown(self.get_files(user_search_dir), label="Choose tags_search.txt", value="")
                            search_refresh_btn = gr.Button("Refresh")
                            use_remove_txt = gr.Checkbox(label="Use tags_remove.txt", value=False)
                            choose_remove_txt = gr.Dropdown(self.get_files(user_remove_dir), label="Choose tags_remove.txt", value="")
                            remove_refresh_btn = gr.Button("Refresh")
                        with gr.Accordion("Extra", open=False):
                            mix_prompt = gr.Checkbox(label="Mix prompts", value=False)
                            mix_amount = gr.Slider(value=2, label="Mix amount", minimum=2, maximum=10, step=1)
                            chaos_mode = gr.Radio(["None", "Chaos", "Less Chaos"], label="Chaos Mode", value="None")
                            chaos_amount = gr.Slider(value=0.5, label="Chaos Amount %", minimum=0.1, maximum=1, step=0.05)
                            negative_mode = gr.Radio(["None", "Negative"], label="Negative Mode", value="None")
                            use_same_seed = gr.Checkbox(label="Use same seed for all pictures", value=False)
                            use_cache = gr.Checkbox(label="Use cache", value=True)

                        # ─── Tag Cache 面板（中文 UI） ────────────────────────
                        with gr.Accordion("Tag 缓存管理", open=False):
                            gr.Markdown("### 📦 批量爬取 Tag 并缓存到本地")
                            with gr.Row():
                                cache_status_display = gr.Textbox(
                                    label="缓存状态", value=tag_cache_manager.get_status(),
                                    interactive=False, lines=1
                                )
                                cache_refresh_status_btn = gr.Button("🔄 刷新状态")

                            gr.Markdown("#### 爬取设置")
                            with gr.Row():
                                cache_pages = gr.Number(label="爬取页数", minimum=1, maximum=100, value=5, step=1, precision=0)
                                cache_append_mode = gr.Checkbox(label="追加模式（不覆盖已有缓存）", value=True)
                            cache_fetch_btn = gr.Button("🚀 开始批量爬取", variant="primary")
                            cache_fetch_result = gr.Textbox(label="爬取结果", interactive=False, lines=2)

                            gr.Markdown("#### 顺序输出")
                            with gr.Row():
                                cache_loop_mode = gr.Checkbox(label="循环播放（到末尾后从头开始）", value=True)
                            with gr.Row():
                                cache_next_btn = gr.Button("▶ 取出下一条")
                                cache_next_set_btn = gr.Button("▶ 取出并设置到提示词", variant="primary")
                            cache_next_output = gr.Textbox(label="当前取出的 Tag", interactive=False, lines=3)
                            
                            gr.Markdown("#### ⚙️ 生成设置")
                            with gr.Row():
                                use_local_cache_gen = gr.Checkbox(label="生成时使用此缓存", value=False)
                                use_local_cache_loop = gr.Checkbox(label="生成时循环读取 (到末尾自动重头)", value=True)

                        # ─── 管理操作面板 ────────────────────────
                        with gr.Accordion("缓存管理操作", open=False):
                            gr.Markdown("### 🔧 搜索、删除、重置缓存")
                            with gr.Row():
                                cache_search_keyword = gr.Textbox(label="搜索关键词", placeholder="输入关键词搜索缓存", lines=1)
                                cache_search_btn = gr.Button("🔍 搜索")
                            cache_search_results = gr.Dataframe(
                                headers=["ID", "Tags"],
                                label="搜索结果",
                                interactive=False,
                                wrap=True
                            )
                            with gr.Row():
                                cache_select_id = gr.Number(label="选择ID", minimum=1, step=1, precision=0)
                                cache_fill_btn = gr.Button("📝 填充到输出")
                                cache_delete_id_btn = gr.Button("🗑️ 删除此条", variant="stop")
                            with gr.Row():
                                cache_reset_btn = gr.Button("🔁 重置索引")
                                cache_delete_btn = gr.Button("🗑️ 删除全部缓存", variant="stop")
                            cache_manage_result = gr.Textbox(label="操作结果", interactive=False, lines=1)

                        # ─── 标签筛选池面板 ────────────────────────────────
                        with gr.Accordion("标签筛选池", open=False):
                            gr.Markdown("### 🎯 从缓存中筛选特定标签，创建自定义标签池")
                            
                            with gr.Row():
                                filter_must_include = gr.Textbox(
                                    label="必须包含（逗号分隔）", 
                                    placeholder="例如: 1girl,blue_eyes",
                                    lines=1
                                )
                                filter_must_exclude = gr.Textbox(
                                    label="必须排除（逗号分隔）", 
                                    placeholder="例如: 2girls,multiple_girls",
                                    lines=1
                                )
                            
                            filter_search_btn = gr.Button("🔍 筛选标签", variant="primary")
                            filter_result_msg = gr.Textbox(label="筛选结果", interactive=False, lines=1)
                            
                            filter_results = gr.Dataframe(
                                headers=["ID", "Tags"],
                                label="筛选结果（可复制 ID）",
                                interactive=False,
                                wrap=True
                            )
                            
                            with gr.Row():
                                filter_selected_ids = gr.Textbox(
                                    label="选中的 ID（逗号分隔）",
                                    placeholder="例如: 1,5,10,23",
                                    lines=1
                                )
                                filter_create_pool_btn = gr.Button("✅ 创建筛选池", variant="primary")
                            
                            with gr.Row():
                                filter_pool_status = gr.Textbox(
                                    label="筛选池状态",
                                    value=tag_cache_manager.get_filtered_pool_status(),
                                    interactive=False,
                                    lines=1
                                )
                                filter_refresh_status_btn = gr.Button("🔄 刷新")
                            
                            with gr.Row():
                                filter_use_pool = gr.Checkbox(label="使用筛选池（而非主缓存）", value=False)
                                filter_switch_btn = gr.Button("🔄 切换")
                            
                            filter_pool_result = gr.Textbox(label="操作结果", interactive=False, lines=1)

        with InputAccordion(False, label="LoRAnado", elem_id=self.elem_id("lo_enable")) as lora_enabled:
            with gr.Group():
                lora_lock_prev = gr.Checkbox(label="Lock previous LoRAs", value=False)
                lora_folder = gr.Textbox(lines=1, label="LoRAs Subfolder")
                lora_amount = gr.Slider(value=1, label="LoRAs Amount", minimum=1, maximum=10, step=1)
            with gr.Group():
                lora_min = gr.Slider(value=-1.0, label="Min LoRAs Weight", minimum=-1.0, maximum=1, step=0.1)
                lora_max = gr.Slider(value=1.0, label="Max LoRAs Weight", minimum=-1.0, maximum=1.0, step=0.1)
                lora_custom_weights = gr.Textbox(lines=1, label="LoRAs Custom Weights")

        search_refresh_btn.click(
            fn=self.refresh_ser,
            inputs=[],
            outputs=[choose_search_txt]
        )

        remove_refresh_btn.click(
            fn=self.refresh_rem,
            inputs=[],
            outputs=[choose_remove_txt]
        )

        # Event handler for loading credentials when booru changes
        booru.change(
            fn=self.load_gelbooru_credentials,
            inputs=[booru],
            outputs=[api_key, user_id, credentials_status, clear_credentials_btn]
        )

        # Event handler for clearing credentials
        clear_credentials_btn.click(
            fn=self.clear_gelbooru_credentials,
            inputs=[booru],
            outputs=[api_key, user_id, credentials_status, clear_credentials_btn]
        )

        # ─── Tag Cache 事件绑定 ───────────────────────────────────────────
        cache_refresh_status_btn.click(
            fn=self._cache_refresh_status,
            inputs=[],
            outputs=[cache_status_display]
        )

        cache_fetch_btn.click(
            fn=self._cache_batch_fetch,
            inputs=[booru, tags, cache_pages, fringe_benefits,
                    remove_bad_tags, remove_tags, shuffle_tags,
                    change_dash, limit_tags, max_tags, mature_rating,
                    api_key, user_id, save_credentials,
                    use_remove_txt, choose_remove_txt, cache_append_mode, tag_categories],
            outputs=[cache_fetch_result, cache_status_display]
        )

        cache_next_btn.click(
            fn=self._cache_get_next,
            inputs=[cache_loop_mode],
            outputs=[cache_next_output, cache_status_display]
        )

        cache_reset_btn.click(
            fn=self._cache_reset_index,
            inputs=[],
            outputs=[cache_manage_result, cache_status_display]
        )

        cache_delete_btn.click(
            fn=self._cache_delete,
            inputs=[],
            outputs=[cache_manage_result, cache_status_display]
        )

        cache_search_btn.click(
            fn=self._cache_search,
            inputs=[cache_search_keyword],
            outputs=[cache_search_results]
        )

        cache_fill_btn.click(
            fn=self._cache_fill_by_id,
            inputs=[cache_select_id],
            outputs=[cache_next_output, cache_manage_result]
        )

        cache_delete_id_btn.click(
            fn=self._cache_delete_by_id,
            inputs=[cache_select_id],
            outputs=[cache_manage_result, cache_status_display]
        )

        # ─── 筛选池事件绑定 ───────────────────────────────────────────
        filter_search_btn.click(
            fn=self._filter_tags,
            inputs=[filter_must_include, filter_must_exclude],
            outputs=[filter_results, filter_result_msg]
        )

        filter_create_pool_btn.click(
            fn=self._create_filtered_pool,
            inputs=[filter_selected_ids],
            outputs=[filter_pool_result, filter_pool_status]
        )

        filter_switch_btn.click(
            fn=self._switch_to_filtered_pool,
            inputs=[filter_use_pool],
            outputs=[filter_pool_result, filter_pool_status]
        )

        filter_refresh_status_btn.click(
            fn=self._get_filtered_pool_status,
            inputs=[],
            outputs=[filter_pool_status]
        )

        target_prompt_box = self.prompt_area[1 if is_img2img else 0]
        if target_prompt_box is None:
            try:
                from modules.ui import txt2img_paste_fields, img2img_paste_fields
                if is_img2img and img2img_paste_fields and 'prompt' in img2img_paste_fields:
                    target_prompt_box = img2img_paste_fields['prompt']
                elif not is_img2img and txt2img_paste_fields and 'prompt' in txt2img_paste_fields:
                    target_prompt_box = txt2img_paste_fields['prompt']
            except Exception:
                target_prompt_box = None

        if target_prompt_box is not None:
            generate_prompt_btn.click(
                fn=self.generate_and_set_prompt,
                inputs=[booru, max_pages, post_id, tags, remove_bad_tags, remove_tags, change_background, change_color, shuffle_tags, change_dash, mix_prompt, mix_amount, use_search_txt, choose_search_txt, use_remove_txt, choose_remove_txt, fringe_benefits, use_cache, api_key, user_id, save_credentials, mature_rating, sorting_order, limit_tags, max_tags, tag_prompt_input, target_prompt_box, tag_categories],
                outputs=[prompt_output, target_prompt_box]
            )

            # Tag Cache: 取出并设置到提示词
            cache_next_set_btn.click(
                fn=self._cache_get_next_and_set,
                inputs=[cache_loop_mode, tag_prompt_input, target_prompt_box],
                outputs=[cache_next_output, cache_status_display, target_prompt_box]
            )
        else:
            generate_prompt_btn.click(
                fn=self.generate_prompts_only,
                inputs=[booru, max_pages, post_id, tags, remove_bad_tags, remove_tags, change_background, change_color, shuffle_tags, change_dash, mix_prompt, mix_amount, use_search_txt, choose_search_txt, use_remove_txt, choose_remove_txt, fringe_benefits, use_cache, api_key, user_id, save_credentials, mature_rating, sorting_order, limit_tags, max_tags, tag_categories],
                outputs=[prompt_output]
            )

            # Tag Cache: 取出到输出框（无法设置到主提示词框）
            cache_next_set_btn.click(
                fn=self._cache_get_next,
                inputs=[cache_loop_mode],
                outputs=[cache_next_output, cache_status_display]
            )

        return [enabled, tags, booru, remove_bad_tags, max_pages, change_dash, same_prompt, fringe_benefits, remove_tags, use_img2img, denoising, use_last_img, change_background, change_color, shuffle_tags, post_id, mix_prompt, mix_amount, chaos_mode, negative_mode, chaos_amount, limit_tags, max_tags, sorting_order, mature_rating, lora_folder, lora_amount, lora_min, lora_max, lora_enabled, lora_custom_weights, lora_lock_prev, use_ip, use_search_txt, use_remove_txt, choose_search_txt, choose_remove_txt, search_refresh_btn, remove_refresh_btn, crop_center, use_deepbooru, type_deepbooru, use_same_seed, use_cache, api_key, user_id, save_credentials, credentials_status, clear_credentials_btn, use_local_cache_gen, use_local_cache_loop, tag_categories]

    def check_orientation(self, img):
        """Check if image is portrait, landscape or square"""
        x, y = img.size
        if x / y > 1.2:
            return [768, 512]
        elif y / x > 1.2:
            return [512, 768]
        else:
            return [768, 768]

    def loranado(self, lora_enabled, lora_folder, lora_amount, lora_min, lora_max, lora_custom_weights, p, lora_lock_prev):
        lora_prompt = ''
        if lora_enabled:
            if lora_lock_prev:
                lora_prompt = self.previous_loras
            else:
                loras = []
                loras = os.listdir(f'{lora_folder}')
                # get only .safetensors files
                loras = [lora.replace('.safetensors', '') for lora in loras if lora.endswith('.safetensors')]
                for l in range(0, lora_amount):
                    lora_weight = 0
                    if lora_custom_weights != '':
                        lora_weight = float(lora_custom_weights.split(',')[l])
                    while lora_weight == 0:
                        lora_weight = round(random.uniform(lora_min, lora_max), 1)
                    lora_prompt += f'<lora:{random.choice(loras)}:{lora_weight}>'
                    self.previous_loras = lora_prompt
        if lora_prompt:
            if isinstance(p.prompt, list):
                for num, pr in enumerate(p.prompt):
                    p.prompt[num] = f'{lora_prompt} {pr}'
            else:
                p.prompt = f'{lora_prompt} {p.prompt}'
        return p

    def before_process(self, p, enabled, tags, booru, remove_bad_tags, max_pages, change_dash, same_prompt, fringe_benefits, remove_tags, use_img2img, denoising, use_last_img, change_background, change_color, shuffle_tags, post_id, mix_prompt, mix_amount, chaos_mode, negative_mode, chaos_amount, limit_tags, max_tags, sorting_order, mature_rating, lora_folder, lora_amount, lora_min, lora_max, lora_enabled, lora_custom_weights, lora_lock_prev, use_ip, use_search_txt, use_remove_txt, choose_search_txt, choose_remove_txt, search_refresh_btn, remove_refresh_btn, crop_center, use_deepbooru, type_deepbooru, use_same_seed, use_cache, api_key, user_id, save_credentials, credentials_status, clear_credentials_btn, use_local_cache_gen, use_local_cache_loop, tag_categories, *args):
        max_pages = int(max_pages)
        if use_cache:
            if HAS_REQUESTS_CACHE and not requests_cache.patcher.is_installed():
                requests_cache.install_cache('ranbooru_cache', backend='sqlite', expire_after=3600)
            elif not HAS_REQUESTS_CACHE:
                print('requests-cache not installed; running without cache')
        else:
            if HAS_REQUESTS_CACHE and requests_cache.patcher.is_installed():
                requests_cache.uninstall_cache()
        
        if enabled:
            if use_local_cache_gen:
                # 任务ID（A1111 通常有 job_timestamp）
                job_id = getattr(shared.state, "job_timestamp", None)
                if job_id is None:
                    # 兜底，避免没有 job_timestamp 时无法去重
                    job_id = f"{getattr(p, 'seed', '')}:{getattr(p, 'n_iter', '')}:{getattr(p, 'batch_size', '')}:{id(p)}"
                # 1) 先拦截 Hires.fix 第二段
                if _is_hires_second_pass(p):
                    print("[Ranbooru] 🟡 Hires.fix 第二段，跳过本地缓存取Tag（不推进索引）")
                    return
                # 2) 再做同任务防重（保险）
                if job_id in self._local_cache_applied_jobs:
                    print(f"[Ranbooru] 🟡 任务 {job_id} 已注入过缓存，跳过重复注入（不推进索引）")
                    return
                self._local_cache_applied_jobs.add(job_id)
                # 可选：防止 set 无限增长
                if len(self._local_cache_applied_jobs) > 200:
                    self._local_cache_applied_jobs.clear()
                print("[Ranbooru] 🟢 已启用本地缓存模式，执行一次缓存注入。")
                # 下面保持你原来的取缓存逻辑不变...
                
                # 计算本次批量生成的总数量 (Batch count * Batch size)
                total_images = p.batch_size * p.n_iter
                cache_prompts = []
                # 为每一张图按顺序取出一个 Tag 串
                for i in range(total_images):
                    # 从缓存管理器获取下一条（自动判断使用主缓存还是筛选池）
                    tags_str, idx, total = tag_cache_manager.get_next_tags_from_pool(loop=use_local_cache_loop)
                    
                    if tags_str is None:
                        print(f"[Ranbooru] ⚠️ 缓存已耗尽 (Index: {idx}/{total})，停止注入。")
                        tags_str = ""
                    else:
                        print(f"[Ranbooru] 📝 正在使用第 {idx} / {total} 条 Tag 数据")
                    
                    # 处理下划线
                    if change_dash:
                        tags_str = tags_str.replace("_", " ")
                    
                    cache_prompts.append(tags_str)
                # 将缓存的 Tag 追加到用户输入的 Prompt 后面
                # 如果 p.prompt 是字符串（单张），转为列表处理；如果是列表（多张），则一一对应
                if isinstance(p.prompt, list):
                    # 如果原 prompt 列表比我们生成的少，就扩展它
                    if len(p.prompt) < total_images:
                        p.prompt = p.prompt * (total_images // len(p.prompt) + 1)
                    
                    # 组合
                    new_prompts = []
                    for j in range(total_images):
                        original = p.prompt[j] if j < len(p.prompt) else ""
                        addition = cache_prompts[j]
                        # 逗号分隔
                        combined = f"{original},{addition}" if original.strip() else addition
                        new_prompts.append(combined)
                    p.prompt = new_prompts
                else:
                    # 单字符串情况
                    new_prompts = []
                    for j in range(total_images):
                        original = p.prompt
                        addition = cache_prompts[j]
                        combined = f"{original},{addition}" if original.strip() else addition
                        new_prompts.append(combined)
                    p.prompt = new_prompts
                # 处理 Lora (保留原有的 Lora 逻辑)
                if lora_enabled:
                    p = self.loranado(lora_enabled, lora_folder, lora_amount, lora_min, lora_max, lora_custom_weights, p, lora_lock_prev)
                
                # 直接结束 before_process，跳过后续所有联网代码
                return
            gelbooru_api_key = None
            gelbooru_user_id = None
            rule34_api_key = None
            rule34_user_id = None
            if booru == 'gelbooru':
                # Use provided credentials or load from saved credentials
                if api_key.strip() and user_id.strip():
                    gelbooru_api_key = api_key.strip()
                    gelbooru_user_id = user_id.strip()
                    
                    # Save credentials if checkbox is checked
                    if save_credentials:
                        credentials_manager.save_booru_credentials('gelbooru', gelbooru_api_key, gelbooru_user_id)
                else:
                    # Try to load saved credentials
                    saved_credentials = credentials_manager.get_booru_credentials('gelbooru')
                    gelbooru_api_key = saved_credentials.get('api_key', '')
                    gelbooru_user_id = saved_credentials.get('user_id', '')
            if booru == 'rule34':
                if api_key.strip() and user_id.strip():
                    rule34_api_key = api_key.strip()
                    rule34_user_id = user_id.strip()
                    if save_credentials:
                        credentials_manager.save_booru_credentials('rule34', rule34_api_key, rule34_user_id)
                else:
                    saved_credentials = credentials_manager.get_booru_credentials('rule34')
                    rule34_api_key = saved_credentials.get('api_key', '')
                    rule34_user_id = saved_credentials.get('user_id', '')
            
            # Initialize APIs
            booru_apis = {
                'gelbooru': Gelbooru(fringe_benefits, gelbooru_api_key, gelbooru_user_id),
                'rule34': Rule34(rule34_api_key, rule34_user_id),
                'safebooru': Safebooru(),
                'danbooru': Danbooru(),
                'konachan': Konachan(),
                'yande.re': Yandere(),
                'aibooru': AIBooru(),
                'xbooru': XBooru(),
                'e621': e621(),
            }
            self.original_prompt = p.prompt
            # Check if compatible
            check_exception(booru, {'tags': tags, 'post_id': post_id})

            # Manage Bad Tags — use global DEFAULT_BAD_TAGS
            bad_tags = []
            if remove_bad_tags:
                bad_tags = list(DEFAULT_BAD_TAGS)

            if ',' in remove_tags:
                bad_tags.extend(remove_tags.split(','))
            else:
                bad_tags.append(remove_tags)

            if use_remove_txt:
                bad_tags.extend(open(os.path.join(user_remove_dir, choose_remove_txt), 'r').read().split(','))

            # Manage Backgrounds
            background_options = {
                'Add Background': ('detailed_background,' + random.choice(["outdoors", "indoors"]), COLORED_BG),
                'Remove Background': ('plain_background,simple_background,' + random.choice(COLORED_BG), ADD_BG),
                'Remove All': ('', COLORED_BG + ADD_BG)
            }

            if change_background in background_options:
                prompt_addition, tags_to_remove = background_options[change_background]
                bad_tags.extend(tags_to_remove)
                p.prompt = f'{p.prompt},{prompt_addition}' if p.prompt else prompt_addition

            # Manage Colors
            color_options = {
                'Colored': BW_BG,
                'Limited Palette': '(limited_palette:1.3)',
                'Monochrome': ','.join(BW_BG)
            }

            if change_color in color_options:
                color_option = color_options[change_color]
                if isinstance(color_option, list):
                    bad_tags.extend(color_option)
                else:
                    p.prompt = f'{p.prompt},{color_option}' if p.prompt else color_option

            if use_search_txt:
                search_tags = open(os.path.join(user_search_dir, choose_search_txt), 'r').read()
                search_tags_r = search_tags.replace(" ", "")
                split_tags = search_tags_r.splitlines()
                filtered_tags = [line for line in split_tags if line.strip()]
                if filtered_tags:
                    selected_tags = random.choice(filtered_tags)
                    tags = f'{tags},{selected_tags}' if tags else selected_tags
                else:
                    print('No tags found in search file; skipping')

            add_tags = '&tags=-animated'
            if tags:
                add_tags += '+' + tags.replace(',', '+')
                if mature_rating != 'All':
                    add_tags += f'+rating:{RATINGS[booru][mature_rating]}'

            # Getting Data
            random_post = {'preview_url': ''}
            prompts = []
            last_img = []
            preview_urls = []
            api_url = booru_apis.get(booru, Gelbooru(fringe_benefits))
            print(f'Using {booru}')

            # Manage Post ID
            if post_id:
                data = api_url.get_post(add_tags, max_pages, post_id)
            else:
                if booru in ['danbooru', 'safebooru', 'aibooru', 'e621'] and tag_categories:
                    data = api_url.get_data(add_tags, max_pages, '', tag_categories)
                else:
                    data = api_url.get_data(add_tags, max_pages)

            print(api_url.booru_url)
            posts = data.get('post', [])
            if not isinstance(posts, list):
                posts = []
            if len(posts) == 0:
                if booru == 'rule34' and add_tags.startswith('&tags=-animated'):
                    fallback_add_tags = '&tags='
                    if tags:
                        fallback_add_tags += tags.replace(',', '+')
                    if mature_rating != 'All':
                        fallback_add_tags += f'+rating:{RATINGS[booru][mature_rating]}'
                    data = api_url.get_data(fallback_add_tags, max_pages)
                    posts = data.get('post', []) if isinstance(data.get('post', []), list) else []
                if len(posts) == 0:
                    print('No posts found; skipping Ranbooru prompt injection.')
                    return p
            COUNT = len(posts)
            # Replace null scores with 0s
            for post in posts:
                if isinstance(post, dict):
                    score = post.get('score')
                    try:
                        post['score'] = int(score) if score not in (None, '') else 0
                    except Exception:
                        post['score'] = 0
            # Sort based on sorting_order
            if sorting_order == 'High Score':
                data['post'] = sorted(posts, key=lambda k: (k.get('score') if isinstance(k, dict) else 0) or 0, reverse=True)
            elif sorting_order == 'Low Score':
                data['post'] = sorted(posts, key=lambda k: (k.get('score') if isinstance(k, dict) else 0) or 0)
            if post_id:
                print(f'Using post ID: {post_id}')
                random_numbers = [0 for _ in range(0, p.batch_size * p.n_iter)]
            else:
                random_numbers = self.random_number(sorting_order, p.batch_size * p.n_iter, len(data['post']))
            for random_number in random_numbers:
                if same_prompt:
                    random_post = data['post'][random_numbers[0]]
                else:
                    if mix_prompt:
                        temp_tags = []
                        mix_max_tags = 0
                        for _ in range(0, mix_amount):
                            random_mix_number = 0 if post_id else self.random_number(sorting_order, 1, len(data['post']))[0]
                            temp_tags.extend(data['post'][random_mix_number]['tags'].split(' '))
                            mix_max_tags = max(mix_max_tags, len(data['post'][random_mix_number]['tags'].split(' ')))
                        # distinct temp_tags
                        temp_tags = list(set(temp_tags))
                        random_post = data['post'][random_number]
                        mix_max_tags = min(max(len(temp_tags), 20), mix_max_tags)
                        random_post['tags'] = ' '.join(random.sample(temp_tags, mix_max_tags))
                    else:
                        try:
                            random_post = data['post'][random_number]
                        except IndexError:
                            raise Exception(
                                "No posts found with those tags. Try lowering the pages or changing the tags.")
                raw_tags = random_post['tags']
                temp_tags = random.sample(raw_tags.split(' '), len(raw_tags.split(' '))) if shuffle_tags else raw_tags.split(' ')
                prompts.append(' '.join(temp_tags))
                preview_urls.append(random_post.get('file_url', 'https://pic.re/image'))
                # Debug picture
                if DEBUG:
                    print(random_post)
            # Get Images
            if use_img2img or use_deepbooru:
                image_urls = [random_post['file_url']] if use_last_img else preview_urls

                for img in image_urls:
                    response = self.fetch_with_retry(img, headers=api_url.headers, timeout=10)
                    last_img.append(Image.open(BytesIO(response.content)))
            new_prompts = []
            # Cleaning Tags
            for prompt in prompts:
                prompt_tags = [tag for tag in html.unescape(prompt).split(' ') if tag.strip() not in bad_tags]
                for bad_tag in bad_tags:
                    if '*' in bad_tag:
                        prompt_tags = [tag for tag in prompt_tags if bad_tag.replace('*', '') not in tag]
                new_prompt = ','.join(prompt_tags)
                if change_dash:
                    new_prompt = new_prompt.replace('_', ' ')
                new_prompts.append(new_prompt)
            prompts = new_prompts
            if len(prompts) == 1:
                print('Processing Single Prompt')
                p.prompt = f"{p.prompt},{prompts[-1]}" if p.prompt else prompts[-1]
                if chaos_mode in ['Chaos', 'Less Chaos']:
                    negative_prompt = '' if chaos_mode == 'Less Chaos' else p.negative_prompt
                    p.prompt, negative_prompt = generate_chaos(p.prompt, negative_prompt, chaos_amount)
                    p.negative_prompt = f"{p.negative_prompt},{negative_prompt}" if p.negative_prompt else negative_prompt
            else:
                print('Processing Multiple Prompts')
                negative_prompts = []
                new_prompts = []
                if chaos_mode == 'Chaos':
                    for prompt in prompts:
                        tmp_prompt, negative_prompt = generate_chaos(prompt, p.negative_prompt, chaos_amount)
                        new_prompts.append(tmp_prompt)
                        negative_prompts.append(negative_prompt)
                    prompts = new_prompts
                    p.negative_prompt = negative_prompts
                elif chaos_mode == 'Less Chaos':
                    for prompt in prompts:
                        tmp_prompt, negative_prompt = generate_chaos(prompt, '', chaos_amount)
                        new_prompts.append(tmp_prompt)
                        negative_prompts.append(negative_prompt)
                    prompts = new_prompts
                    p.negative_prompt = [p.negative_prompt + ',' + negative_prompt for negative_prompt in negative_prompts]
                else:
                    p.negative_prompt = [p.negative_prompt for _ in range(0, p.batch_size * p.n_iter)]
                p.prompt = prompts if not p.prompt else [f"{p.prompt},{prompt}" for prompt in prompts]
                if use_img2img:
                    if len(last_img) < p.batch_size * p.n_iter:
                        last_img = [last_img[0] for _ in range(0, p.batch_size * p.n_iter)]
            if negative_mode == 'Negative':
                # remove tags from p.prompt using tags from the original prompt
                orig_list = self.original_prompt.split(',')
                if isinstance(p.prompt, list):
                    new_positive_prompts = []
                    new_negative_prompts = []
                    for pr, npp in zip(p.prompt, p.negative_prompt):
                        clean_prompt = pr.split(',')
                        clean_prompt = [tag for tag in clean_prompt if tag not in orig_list]
                        clean_prompt = ','.join(clean_prompt)
                        new_positive_prompts.append(self.original_prompt)
                        new_negative_prompts.append(f'{npp},{clean_prompt}')
                    p.prompt = new_positive_prompts
                    p.negative_prompt = new_negative_prompts
                else:
                    clean_prompt = p.prompt.split(',')
                    clean_prompt = [tag for tag in clean_prompt if tag not in orig_list]
                    clean_prompt = ','.join(clean_prompt)
                    p.negative_prompt = f'{p.negative_prompt},{clean_prompt}'
                    p.prompt = self.original_prompt
            if negative_mode == 'Negative' or chaos_mode in ['Chaos', 'Less Chaos']:
                # NEGATIVE PROMPT FIX
                if isinstance(p.negative_prompt, str):
                    p.negative_prompt = [p.negative_prompt for _ in range(0, p.batch_size * p.n_iter)]
                neg_prompt_tokens = []
                for pr in p.negative_prompt:
                    neg_prompt_tokens.append(model_hijack.get_prompt_lengths(pr)[1])
                if len(set(neg_prompt_tokens)) != 1:
                    print('Padding negative prompts')
                    max_tokens = max(neg_prompt_tokens)
                    for num, neg in enumerate(neg_prompt_tokens):
                        while neg < max_tokens:
                            p.negative_prompt[num] += random.choice(p.negative_prompt[num].split(','))
                            # p.negative_prompt[num] += '_'
                            neg = model_hijack.get_prompt_lengths(p.negative_prompt[num])[1]

            if limit_tags < 1:
                if isinstance(p.prompt, list):
                    p.prompt = [limit_prompt_tags(pr, limit_tags, 'Limit') for pr in p.prompt]
                else:
                    p.prompt = limit_prompt_tags(p.prompt, limit_tags, 'Limit')

            if max_tags > 0:
                if isinstance(p.prompt, list):
                    p.prompt = [limit_prompt_tags(pr, max_tags, 'Max') for pr in p.prompt]
                else:
                    p.prompt = limit_prompt_tags(p.prompt, max_tags, 'Max')

            if use_same_seed:
                p.seed = random.randint(0, 2 ** 32 - 1) if p.seed == -1 else p.seed
                p.seed = [p.seed] * p.batch_size

            # LORANADO
            p = self.loranado(lora_enabled, lora_folder, lora_amount, lora_min, lora_max, lora_custom_weights, p, lora_lock_prev)
            if use_deepbooru and not use_img2img:
                self.last_img = last_img
                tagged_prompts = self.use_autotagger('deepbooru')

                if isinstance(p.prompt, list):
                    p.prompt = [modify_prompt(pr, tagged_prompts[num], type_deepbooru) for num, pr in enumerate(p.prompt)]
                    p.prompt = [remove_repeated_tags(pr) for pr in p.prompt]
                else:
                    p.prompt = modify_prompt(p.prompt, tagged_prompts, type_deepbooru)
                    p.prompt = remove_repeated_tags(p.prompt)

            if use_img2img:
                if not use_ip:
                    self.real_steps = p.steps
                    p.steps = 1
                    self.last_img = last_img
                if use_ip:
                    controlNetModule = importlib.import_module('extensions.sd-webui-controlnet.scripts.external_code', 'external_code')
                    controlNetList = controlNetModule.get_all_units_in_processing(p)
                    copied_network = controlNetList[0].__dict__.copy()
                    copied_network['enabled'] = True
                    copied_network['weight'] = denoising
                    array_img = np.array(last_img[0])
                    copied_network['image']['image'] = array_img
                    copied_networks = [copied_network] + controlNetList[1:]
                    controlNetModule.update_cn_script_in_processing(p, copied_networks)

        elif lora_enabled:
            p = self.loranado(lora_enabled, lora_folder, lora_amount, lora_min, lora_max, lora_custom_weights, p, lora_lock_prev)

    def postprocess(self, p, processed, enabled, tags, booru, remove_bad_tags, max_pages, change_dash, same_prompt, fringe_benefits, remove_tags, use_img2img, denoising, use_last_img, change_background, change_color, shuffle_tags, post_id, mix_prompt, mix_amount, chaos_mode, negative_mode, chaos_amount, limit_tags, max_tags, sorting_order, mature_rating, lora_folder, lora_amount, lora_min, lora_max, lora_enabled, lora_custom_weights, lora_lock_prev, use_ip, use_search_txt, use_remove_txt, choose_search_txt, choose_remove_txt, search_refresh_btn, remove_refresh_btn, crop_center, use_deepbooru, type_deepbooru, use_same_seed, use_cache, api_key, user_id, save_credentials, credentials_status, clear_credentials_btn, use_local_cache_gen, use_local_cache_loop, tag_categories, *args):
        if use_img2img and not use_ip and enabled:
            print('Using pictures')
            if crop_center:
                width, height = p.width, p.height
                self.last_img = [resize_image(img, width, height, cropping=True) for img in self.last_img]
            else:
                width, height = self.check_orientation(self.last_img[0])
            final_prompts = p.prompt
            if use_deepbooru:
                tagged_prompts = self.use_autotagger('deepbooru')
                if isinstance(p.prompt, list):
                    final_prompts = [modify_prompt(pr, tagged_prompts[num], type_deepbooru) for num, pr in enumerate(p.prompt)]
                    final_prompts = [remove_repeated_tags(pr) for pr in final_prompts]
                else:
                    final_prompts = modify_prompt(p.prompt, tagged_prompts, type_deepbooru)
                    final_prompts = remove_repeated_tags(final_prompts)
            p = StableDiffusionProcessingImg2Img(
                sd_model=shared.sd_model,
                outpath_samples=shared.opts.outdir_samples or shared.opts.outdir_img2img_samples,
                outpath_grids=shared.opts.outdir_grids or shared.opts.outdir_img2img_grids,
                prompt=final_prompts,
                negative_prompt=p.negative_prompt,
                seed=p.seed,
                sampler_name=p.sampler_name,
                scheduler=p.scheduler,
                batch_size=p.batch_size,
                n_iter=p.n_iter,
                steps=self.real_steps,
                cfg_scale=p.cfg_scale,
                width=width,
                height=height,
                init_images=self.last_img,
                denoising_strength=denoising,
            )
            proc = process_images(p)
            processed.images = proc.images
            processed.infotexts = proc.infotexts
            if use_last_img:
                processed.images.append(self.last_img[0])
        else:
            if hasattr(self, 'last_img') and self.last_img:
                for img in self.last_img:
                    processed.images.append(img)

    def generate_prompts_only(self, booru, max_pages, post_id, tags, remove_bad_tags, remove_tags, change_background, change_color, shuffle_tags, change_dash, mix_prompt, mix_amount, use_search_txt, choose_search_txt, use_remove_txt, choose_remove_txt, fringe_benefits, use_cache, api_key, user_id, save_credentials, mature_rating, sorting_order, limit_tags, max_tags, tag_categories):
        max_pages = int(max_pages)
        if use_cache:
            if HAS_REQUESTS_CACHE and not requests_cache.patcher.is_installed():
                requests_cache.install_cache('ranbooru_cache', backend='sqlite', expire_after=3600)
        else:
            if HAS_REQUESTS_CACHE and requests_cache.patcher.is_installed():
                requests_cache.uninstall_cache()

        gelbooru_api_key = None
        gelbooru_user_id = None
        rule34_api_key = None
        rule34_user_id = None
        if booru == 'gelbooru':
            if api_key.strip() and user_id.strip():
                gelbooru_api_key = api_key.strip()
                gelbooru_user_id = user_id.strip()
                if save_credentials:
                    credentials_manager.save_booru_credentials('gelbooru', gelbooru_api_key, gelbooru_user_id)
            else:
                saved_credentials = credentials_manager.get_booru_credentials('gelbooru')
                gelbooru_api_key = saved_credentials.get('api_key', '')
                gelbooru_user_id = saved_credentials.get('user_id', '')
        if booru == 'rule34':
            if api_key.strip() and user_id.strip():
                rule34_api_key = api_key.strip()
                rule34_user_id = user_id.strip()
                if save_credentials:
                    credentials_manager.save_booru_credentials('rule34', rule34_api_key, rule34_user_id)
            else:
                saved_credentials = credentials_manager.get_booru_credentials('rule34')
                rule34_api_key = saved_credentials.get('api_key', '')
                rule34_user_id = saved_credentials.get('user_id', '')

        booru_apis = {
            'gelbooru': Gelbooru(fringe_benefits, gelbooru_api_key, gelbooru_user_id),
            'rule34': Rule34(rule34_api_key, rule34_user_id),
            'safebooru': Safebooru(),
            'danbooru': Danbooru(),
            'konachan': Konachan(),
            'yande.re': Yandere(),
            'aibooru': AIBooru(),
            'xbooru': XBooru(),
            'e621': e621(),
        }

        # Use global DEFAULT_BAD_TAGS
        bad_tags = []
        if remove_bad_tags:
            bad_tags = list(DEFAULT_BAD_TAGS)
        if ',' in remove_tags:
            bad_tags.extend(remove_tags.split(','))
        else:
            if remove_tags:
                bad_tags.append(remove_tags)
        if use_remove_txt:
            bad_tags.extend(open(os.path.join(user_remove_dir, choose_remove_txt), 'r').read().split(','))

        prompt_addition = ''
        background_options = {
            'Add Background': ('detailed_background,' + random.choice(["outdoors", "indoors"]), COLORED_BG),
            'Remove Background': ('plain_background,simple_background,' + random.choice(COLORED_BG), ADD_BG),
            'Remove All': ('', COLORED_BG + ADD_BG)
        }
        if change_background in background_options:
            pa, tags_to_remove = background_options[change_background]
            bad_tags.extend(tags_to_remove)
            prompt_addition = pa

        color_options = {
            'Colored': BW_BG,
            'Limited Palette': '(limited_palette:1.3)',
            'Monochrome': ','.join(BW_BG)
        }
        if change_color in color_options:
            co = color_options[change_color]
            if isinstance(co, list):
                bad_tags.extend(co)
            else:
                prompt_addition = f'{prompt_addition},{co}' if prompt_addition else co

        if use_search_txt:
            search_tags = open(os.path.join(user_search_dir, choose_search_txt), 'r').read()
            search_tags_r = search_tags.replace(' ', '')
            split_tags = search_tags_r.splitlines()
            filtered_tags = [line for line in split_tags if line.strip()]
            if filtered_tags:
                selected_tags = random.choice(filtered_tags)
                tags = f'{tags},{selected_tags}' if tags else selected_tags

        add_tags = '&tags=-animated'
        if tags:
            add_tags += '+' + tags.replace(',', '+')
            if mature_rating != 'All':
                add_tags += f'+rating:{RATINGS[booru][mature_rating]}'

        api_url = booru_apis.get(booru, Gelbooru(fringe_benefits, gelbooru_api_key, gelbooru_user_id))
        if post_id:
            data = api_url.get_post(add_tags, max_pages, post_id)
        else:
            if booru in ['danbooru', 'safebooru', 'aibooru', 'e621'] and tag_categories:
                data = api_url.get_data(add_tags, max_pages, '', tag_categories)
            else:
                data = api_url.get_data(add_tags, max_pages)
        posts = data.get('post', [])
        if not isinstance(posts, list):
            posts = []
        if len(posts) == 0 and booru == 'rule34' and add_tags.startswith('&tags=-animated'):
            ft = '&tags='
            if tags:
                ft += tags.replace(',', '+')
            if mature_rating != 'All':
                ft += f'+rating:{RATINGS[booru][mature_rating]}'
            data = api_url.get_data(ft, max_pages)
            posts = data.get('post', []) if isinstance(data.get('post', []), list) else []
        if len(posts) == 0:
            return '未找到符合条件的帖子'

        for post in posts:
            if isinstance(post, dict):
                s = post.get('score')
                try:
                    post['score'] = int(s) if s not in (None, '') else 0
                except Exception:
                    post['score'] = 0
        if sorting_order == 'High Score':
            posts = sorted(posts, key=lambda k: (k.get('score') if isinstance(k, dict) else 0) or 0, reverse=True)
        elif sorting_order == 'Low Score':
            posts = sorted(posts, key=lambda k: (k.get('score') if isinstance(k, dict) else 0) or 0)

        rn = self.random_number(sorting_order, 1, len(posts))[0]
        if mix_prompt:
            temp_tags = []
            mt = 0
            for _ in range(0, mix_amount):
                rm = self.random_number(sorting_order, 1, len(posts))[0]
                temp_tags.extend(posts[rm]['tags'].split(' '))
                mt = max(mt, len(posts[rm]['tags'].split(' ')))
            temp_tags = list(set(temp_tags))
            rp = posts[rn]
            mt = min(max(len(temp_tags), 20), mt)
            rp['tags'] = ' '.join(random.sample(temp_tags, mt))
        else:
            rp = posts[rn]

        raw_tags = rp['tags']
        temp_tags = random.sample(raw_tags.split(' '), len(raw_tags.split(' '))) if shuffle_tags else raw_tags.split(' ')
        tag_list = [t for t in temp_tags if t.strip() not in bad_tags]
        for bt in bad_tags:
            if '*' in bt:
                tag_list = [t for t in tag_list if bt.replace('*', '') not in t]
        prompt = ','.join(tag_list)
        if change_dash:
            prompt = prompt.replace('_', ' ')
        if limit_tags < 1:
            prompt = limit_prompt_tags(prompt, limit_tags, 'Limit')
        if max_tags > 0:
            prompt = limit_prompt_tags(prompt, max_tags, 'Max')
        final_prompt = f'{prompt_addition},{prompt}' if prompt_addition else prompt
        return final_prompt

    def generate_and_set_prompt(self, booru, max_pages, post_id, tags, remove_bad_tags, remove_tags, change_background, change_color, shuffle_tags, change_dash, mix_prompt, mix_amount, use_search_txt, choose_search_txt, use_remove_txt, choose_remove_txt, fringe_benefits, use_cache, api_key, user_id, save_credentials, mature_rating, sorting_order, limit_tags, max_tags, tag_prompt_text, current_prompt, tag_categories):
        final_prompt = self.generate_prompts_only(booru, max_pages, post_id, tags, remove_bad_tags, remove_tags, change_background, change_color, shuffle_tags, change_dash, mix_prompt, mix_amount, use_search_txt, choose_search_txt, use_remove_txt, choose_remove_txt, fringe_benefits, use_cache, api_key, user_id, save_credentials, mature_rating, sorting_order, limit_tags, max_tags, tag_categories)
        if not final_prompt or final_prompt.strip() == '' or final_prompt == '未找到符合条件的帖子':
            return final_prompt, current_prompt
        if tag_prompt_text and tag_prompt_text.strip():
            combined_prompt = f"{tag_prompt_text.strip()}{',' if final_prompt else ''}{final_prompt}"
        else:
            combined_prompt = final_prompt
        return combined_prompt, combined_prompt

    def random_number(self, sorting_order, size, count):
        """Generates random numbers based on the sorting_order

        Args:
            sorting_order (str): the sorting order. It can be 'Random', 'High Score' or 'Low Score'
            size (int): the amount of random numbers to generate

        Returns:
            list: the random numbers
        """
        if count <= 0:
            raise Exception("No posts found with those tags. Try lowering the pages or changing the tags.")
        if count > POST_AMOUNT:
            count = POST_AMOUNT
        weights = np.arange(count, 0, -1)
        weights = weights / weights.sum()
        if sorting_order in ('High Score', 'Low Score'):
            if size <= count:
                random_numbers = np.random.choice(np.arange(count), size=size, p=weights, replace=False).tolist()
            else:
                random_numbers = np.random.choice(np.arange(count), size=size, p=weights, replace=True).tolist()
        else:
            if size <= count:
                random_numbers = random.sample(range(count), size)
            else:
                random_numbers = np.random.choice(np.arange(count), size=size, replace=True).tolist()
        return random_numbers

    def use_autotagger(self, model):
        """Use the autotagger to tag the images

        Args:
            model (str): the model to use. Right now only 'deepbooru' is supported

        Returns:
            list: the tagged prompts
        """
        if model == 'deepbooru':
            if isinstance(self.original_prompt, str):
                orig_prompt = [self.original_prompt]
            else:
                orig_prompt = self.original_prompt
            deepbooru.model.start()
            final_prompts = [prompt + ',' + deepbooru.model.tag_multi(img) for img, prompt in zip(self.last_img, orig_prompt)]
            deepbooru.model.stop()
            return final_prompts
