__author__ = 'andr0day'

import os

from watchdog.observers import *
from watchdog.events import *


class IgnoreHandler(FileSystemEventHandler):
    path = os.getcwd()+'/ignore'
    start_with = []
    pat = []

    def __init__(self):
        pass

    def start_watch(self):
        observer = Observer()
        observer.schedule(self, self.path, recursive=True)
        observer.start()
        # observer.join()
        pass

    def on_modified(self, event):
        self.start_with = self.read_file(self.path+"/start.txt")
        self.pat = self.read_file(self.path+"/pat.txt")
        pass

    def read_file(self, path):
        return open(path, "r").readlines()
        pass
    pass
