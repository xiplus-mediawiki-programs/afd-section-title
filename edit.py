# -*- coding: utf-8 -*-
import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Dict, List

os.environ['PYWIKIBOT_DIR'] = os.path.dirname(os.path.realpath(__file__))
import pywikibot
from pywikibot.data.api import Request
from pywikibot.textlib import extract_sections

from config import config_page_name  # pylint: disable=E0611,W0614

parser = argparse.ArgumentParser()
parser.add_argument('pagename', nargs='?')
parser.add_argument('-c', '--confirm', action='store_true')
parser.add_argument('-d', '--debug', action='store_const', dest='loglevel', const=logging.DEBUG, default=logging.INFO)
args = parser.parse_args()

logger = logging.getLogger('archive_ar')
formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(formatter)
logger.addHandler(stdout_handler)
logger.setLevel(args.loglevel)
logger.debug('args: %s', args)

site = pywikibot.Site()
site.login()

config_page = pywikibot.Page(site, config_page_name)
cfg = config_page.text
cfg = json.loads(cfg)
logger.debug('config: %s', json.dumps(cfg, indent=4, ensure_ascii=False))

if not cfg['enable']:
    print('disabled')
    exit()


normalized_titles = dict()
converted_titles = dict()
redirect_titles = dict()

afd_titles = set()
for page in pywikibot.Category(site, 'Category:所有刪除候選').members():
    afd_titles.add(page.title())
logger.debug('afd_titles: %d', len(afd_titles))


def check_title(old_title):
    mode = []

    # 順序不得更改
    if old_title in normalized_titles:  # 命名空間等
        mode.append('normalized')
        old_title = normalized_titles[old_title]
    new_title = old_title
    if new_title in converted_titles:  # 繁簡轉換
        mode.append('converted')
        new_title = converted_titles[new_title]
    if new_title in redirect_titles:  # 重定向
        mode.append('redirects')
        new_title = redirect_titles[new_title]

    if 'redirects' in mode:
        if old_title in afd_titles or re.search('^MediaWiki:', old_title) or re.search('\.(js|css|json)$', old_title):
            mode.append('vfd_on_source')
        if new_title in afd_titles or re.search('^MediaWiki:', new_title) or re.search('\.(js|css|json)$', new_title):
            mode.append('vfd_on_target')
    else:
        if new_title in afd_titles or re.search('^MediaWiki:', new_title) or re.search('\.(js|css|json)$', new_title):
            mode.append('vfd_on_source')
    if 'vfd_on_source' not in mode and 'vfd_on_target' not in mode:
        page = pywikibot.Page(site, new_title)
        try:
            if page.exists():
                mode.append('no_vfd')
            else:
                mode.append('vfd_on_source')
        except pywikibot.exceptions.InvalidTitleError:
            mode.append('vfd_on_source')
    return {'title': new_title, 'mode': mode}


def appendComment(text, mode):
    if 'A2093064-bot' not in text:
        append_text = []
        if 'fix' in mode:
            comment = []
            if 'redirects' in mode and isinstance(cfg['comment_fix']['redirects'], str):
                comment.append(cfg['comment_fix']['redirects'])
                logger.debug('\tcomment_fix - redirects')
            if 'converted' in mode and isinstance(cfg['comment_fix']['converted'], str):
                comment.append(cfg['comment_fix']['converted'])
                logger.debug('\tcomment_fix - converted')
            if 'normalized' in mode and isinstance(cfg['comment_fix']['normalized'], str):
                comment.append(cfg['comment_fix']['normalized'])
                logger.debug('\tcomment_fix - normalized')
            if len(comment) > 0:
                append_text.append(cfg['comment_fix']['main'].format(
                    ''.join(comment)))
                logger.debug('\tcomment_fix - redirects')
        if 'no_vfd' in mode:
            append_text.append(cfg['comment_vfd'])
            logger.debug('\tcomment_vfd')
        if len(append_text) > 0:
            append_text = '\n'.join(append_text)
            hr = '\n----'
            if hr in text:
                temp = text.split(hr)
                text = hr.join(temp[:-1]) + '\n' + append_text + hr + temp[-1]
            else:
                text += append_text + '\n'
    return text


def escapeEqualSign(titlelist):
    anyEqual = any(['=' in title for title in titlelist])
    if anyEqual:
        newtitlelist = []
        for i, title in enumerate(titlelist, 1):
            newtitlelist.append('{}={}'.format(i, title))
        return newtitlelist
    return titlelist


def fix(pagename):
    if re.search(r'\d{4}/\d{2}/\d{2}', pagename):
        pagename = 'Wikipedia:頁面存廢討論/記錄/' + pagename

    logger.debug('-' * 50)
    logger.info('running for ' + pagename)

    afdpage = pywikibot.Page(site, pagename)
    text = afdpage.text

    header, threads, footer = extract_sections(text, site)

    section_titles: Dict[int, List[str]] = dict()
    for sec_id, section in enumerate(threads):
        if re.search(r'{{\s*(delh|TalkendH)\s*(\||}})', section.content, re.IGNORECASE) is not None:
            logger.debug('%s closed, skip', section.title.strip('= '))
            continue

        heading = section.title.strip('= ')

        m = re.search(r'^\[\[([^\]]+)\]\]$', heading, re.IGNORECASE)
        if m:
            section_titles[sec_id] = [m.group(1)]
            continue

        m = re.search(r'^(\[\[[^\]]+\]\][、， ])+\[\[[^\]]+\]\]$', heading, re.IGNORECASE)
        if m:
            title_list = re.sub(r'\]\][， ]\[\[', ']]、[[', heading).split('、')
            section_titles[sec_id] = []
            for title in title_list:
                section_titles[sec_id].append(title.strip('[]'))
            continue

        m = re.search(r'^{{al\|((?:[^\]]+\|)+[^\]]+)}}$', heading, re.IGNORECASE)
        if m is not None:
            title_list = m.group(1).split('|')
            section_titles[sec_id] = []
            for title in title_list:
                m = re.search(r'^\s*\d+\s*=\s*(.+)$', title)
                if m:
                    section_titles[sec_id].append(m.group(1))
                else:
                    section_titles[sec_id].append(title)
            continue

        logger.debug('%s unknown format, skip', heading)

    all_titles = [title for section in section_titles.values() for title in section]  # flatten
    BATCH_SIZE = 50
    for i in range(0, len(all_titles), BATCH_SIZE):
        params = {
            'action': 'query',
            'titles': '|'.join(all_titles[i:i + BATCH_SIZE]),
            'redirects': 1,
            'converttitles': 1,
            'format': 'json',
            'formatversion': 2,
        }
        logger.debug('params: %s', params)
        r = Request(site=site, parameters=params)
        data = r.submit()
        for item in data['query'].get('normalized', []):
            normalized_titles[item['from']] = item['to']
        for item in data['query'].get('converted', []):
            converted_titles[item['from']] = item['to']
        for item in data['query'].get('redirects', []):
            redirect_titles[item['from']] = item['to']

    new_text = header.strip() + '\n\n'
    for sec_id, section in enumerate(threads):
        sec_content = section.content.strip()
        if sec_content:
            sec_content += '\n'

        if sec_id not in section_titles:
            new_text += section.title + '\n' + sec_content + '\n'
            continue

        m = re.search(r'^(=+)', section.title)
        if m:
            old_level = m.group(1)
        else:
            logger.warning('fail to check section level {}'.format(section.title))
            new_text += section.title + '\n' + sec_content + '\n'
            continue

        new_titles = []
        mode = []
        for old_title in section_titles[sec_id]:
            convert = check_title(old_title)
            new_title = old_title

            if (('redirects' in convert['mode'] and 'vfd_on_target' in convert['mode'])
                    or ('redirects' not in convert['mode'])):
                new_title = convert['title']
                if old_title[0] == ':':
                    new_title = ':' + new_title
            if old_title != new_title:
                mode.append('fix')
            mode += convert['mode']

            new_titles.append(new_title)

        old_heading = section.title.strip('= ')
        logger.debug('%s: %s', old_heading, ', '.join(mode))
        new_titles = escapeEqualSign(new_titles)

        new_heading = ''
        if len(new_titles) == 1:
            new_heading = '[[{}]]'.format(new_titles[0])
        else:
            new_heading = '{{al|' + '|'.join(new_titles) + '}}'
        if old_heading != new_heading:
            logger.info('change heading to %s', new_heading)

        new_text += '{0} {1} {0}\n'.format(old_level, new_heading)
        if len(new_titles) == 1:
            old_norm_heading = old_heading.replace('_', ' ').replace('&#39;', "'")
            if old_norm_heading != new_heading:
                sec_content = '{{formerly|' + old_heading + '}}\n' + sec_content
        sec_content = appendComment(sec_content, mode)
        new_text += sec_content + '\n'

    new_text += footer.strip()

    if re.sub(r'\s+', '', afdpage.text) == re.sub(r'\s+', '', new_text):
        logger.info('nothing changed')
        with open('out.txt', 'w', encoding='utf8') as f:
            f.write(new_text)
        return

    summary = cfg['summary']
    if args.confirm or args.loglevel <= logging.DEBUG:
        pywikibot.showDiff(afdpage.text, new_text)
        logger.info('summary: %s', summary)

    save = True
    if args.confirm:
        save = pywikibot.input_yn('Save changes for main page?', 'Y')
    if save:
        logger.info('save changes')
        afdpage.text = new_text
        afdpage.save(summary=summary, minor=False)
    else:
        with open('out.txt', 'w', encoding='utf8') as f:
            f.write(new_text)
        logger.info('skip save')


if args.pagename:
    fix(args.pagename)
else:
    logger.debug('run past %s days', cfg['run_past_days'])
    for delta in range(cfg['run_past_days']):
        rundate = datetime.now() - timedelta(days=delta)
        pagename = rundate.strftime('%Y/%m/%d')
        fix(pagename)
