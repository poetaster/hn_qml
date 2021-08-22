import time
import json
import os
import queue
import html
import threading

import requests

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, NamedTuple
from html.parser import HTMLParser

import pyotherside

session = requests.Session()
NUM_BG_THREADS = 4
SEARCH_URL = 'https://hn.algolia.com/api/v1/search'
ITEMS_URL = 'https://hn.algolia.com/api/v1/items/'
TOP_STORIES_URL = 'https://hacker-news.firebaseio.com/v0/topstories.json'

CONFIG_PATH = Path('/home/phablet/.config/hnr.davidv.dev/')

if not CONFIG_PATH.exists():
    CONFIG_PATH.mkdir()
with (CONFIG_PATH / 'test.txt').open('w') as fd:
    fd.write('hi!')

thread_q = queue.Queue()

Comment = NamedTuple(
    "Comment",
    [
        ("thread_id", str),
        ("parent_id", str),
        ("comment_id", str),
        ("user", str),
        ("markup", str),
        ("kids", List[int]),
        ("dead", bool),
        ("deleted", bool),
        ("age", str),
        ("depth", int),
        ("threadVisible", bool),
    ],
)
Story = NamedTuple(
    "Story",
    [
        ("story_id", str),
        ("title", str),
        ("url", str),
        ("url_domain", str),
        ("kids", List[int]),
        ("comment_count", int),
        ("score", int),
        ("initialized", bool),
        ("highlight", int),
    ],
)

def do_work():
    while True:
        t = thread_q.get()
        fetch_and_signal(t)
        time.sleep(0.05)

for i in range(NUM_BG_THREADS):
    t = threading.Thread(target=do_work)
    t.daemon = True
    t.start()

def fetch_and_signal(_id):
    pyotherside.send("thread-pop", get_story_stub(_id))

def top_stories():
    r = session.get(TOP_STORIES_URL)
    data = r.json()
    return [
        Story(story_id=str(i), title="..", url="", url_domain="..", kids=[], comment_count=0, score=0, initialized=False, highlight=0)._asdict()
        for i in data
    ]

def get_story_stub(_id):
    data = get_id(_id)
    s = Story(story_id=str(_id),
                 title=data['title'],
                 url=data.get('url', 'self'),
                 url_domain=get_domain(data.get('url', '//self')),
                 kids=[],
                 comment_count=data.get('descendants', 0),
                 score=data['score'],
                 initialized=True,
                 highlight=0)._asdict()
    return s

def get_id(_id):
    _id = str(_id)
    r = session.get("https://hacker-news.firebaseio.com/v0/item/" + _id + ".json")
    data = r.json()
    return data

def get_domain(url):
    return url.split("/")[2]

def flatten(children, depth):
    res = []
    for c in children:
        _k = c.pop('children')
        c['depth'] = depth
        c['hasKids'] = len(_k) > 0
        res.append(c)
        res.extend(flatten(_k, depth + 1))
    return res

def get_story(_id) -> Story:
    _id = str(_id)

    t = time.time()
    raw_data = session.get(ITEMS_URL + _id).json()
    print('Fetching story took', time.time() - t, flush=True)
    if raw_data['type'] == 'comment':
        # app is opening a link directly to a comment
        story_id = session.get(ITEMS_URL + _id).json()['story_id']
        story = get_story(story_id)
        print('Fetched a comment..', _id)
        story['highlight'] = int(_id)
        return story
    else:
        score = raw_data["points"]
        title = raw_data["title"]

    kids = raw_data.get("children", [])

    if raw_data.get("url"):
        url = raw_data["url"]
        url_domain = get_domain(raw_data["url"])
    else:
        url = "self"
        url_domain = "self"

    kids = flatten(kids, 0)
    kids = [{'threadVisible': True,
             'age': _to_relative_time(k['created_at_i']),
             'markup': html.unescape(k['text'] or ''),
             'comment_id': k['id'],
             **k}
             for k in kids if k['text'] or k['hasKids']]
    story = Story(
        story_id=_id, title=title, url=url, url_domain=url_domain,
        kids=kids, comment_count=len(kids),
        score=score, initialized=True, highlight='',
    )
    return story._asdict()


def bg_fetch_story(story_id):
    thread_q.put(story_id)

def _to_relative_time(tstamp):
   now = time.time()
   delta = now - tstamp
   if delta < 0:
       return 'in the future'

   if delta < 60:
       return str(int(delta)) + 's ago'
   delta /= 60
   if delta < 60:
       return str(int(delta)) + 'm ago'
   delta /= 60
   if delta < 24:
       return str(int(delta)) + 'h ago'
   delta /= 24
   if delta < 365:
       return str(int(delta)) + 'd ago'
   delta /= 365
   return str(int(delta)) + 'y ago'

def search(query, tags='story'):
    r = session.get(SEARCH_URL, params={'query': query, 'tags': tags, 'hitsPerPage': 50})
    r.raise_for_status()
    data = r.json()['hits']

    return [
        Story(story_id=str(i['objectID']),
              title=i['title'],
              url=i['url'],
              url_domain=get_domain(i['url'] or '//self'),
              kids=[],
              comment_count=i['num_comments'],
              score=i['points'],
              initialized=False,
              highlight=0)._asdict()
        for i in data
    ]

def html_to_plaintext(h):
    class HTMLFilter(HTMLParser):
        text = ""
        parsing_anchor = False

        def handle_starttag(self, tag, attrs):
            if tag == 'p':
                self.text += '\n'
            elif tag == 'a':
                self.text += dict(attrs)['href']
                self.parsing_anchor = True
        def handle_endtag(self, tag):
            if tag == 'a':
                self.parsing_anchor = False
        def handle_data(self, data):
            if not self.parsing_anchor:
                self.text += data

    f = HTMLFilter()
    f.feed(h)
    return f.text
