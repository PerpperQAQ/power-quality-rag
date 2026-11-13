"""Extract readable static HTML without executing code or fetching resources."""
from html.parser import HTMLParser
import re

class Reader(HTMLParser):
    ignored={'script','style','noscript','svg','nav','footer','head','template','iframe'}
    blocks={'p','div','section','article','main','blockquote','pre','ul','ol','tr'}
    def __init__(self):
        super().__init__(convert_charrefs=True);self.stack=[];self.parts=[];self.cells=0
    def handle_starttag(self,tag,attrs):
        if self.stack:
            if tag in self.ignored:self.stack.append(tag)
            return
        if tag in self.ignored:self.stack.append(tag);return
        if tag in self.blocks or tag in {'h1','h2','h3','h4','h5','h6','li','br'}:self.parts.append('\n')
        if tag in {'h1','h2','h3','h4','h5','h6'}:self.parts.append('#'*int(tag[1])+' ')
        if tag=='li':self.parts.append('- ')
        if tag=='tr':self.cells=0
        if tag in {'td','th'}:
            if self.cells:self.parts.append(' | ')
            self.cells+=1
    def handle_endtag(self,tag):
        if self.stack:
            if self.stack[-1]==tag:self.stack.pop()
            return
        if tag in self.blocks or tag in {'h1','h2','h3','h4','h5','h6','li'}:self.parts.append('\n')
    def handle_data(self,data):
        if not self.stack:self.parts.append(data)

def extract_html(raw):
    parser=Reader();parser.feed(raw);parser.close()
    lines=[re.sub(r'\s+',' ',line).strip() for line in ''.join(parser.parts).splitlines()]
    return '\n\n'.join(line for line in lines if line)
