#!/usr/bin/env python3

import sys

R = '\033[31m'
G = '\033[32m'
C = '\033[36m'
W = '\033[0m'
Y = '\033[33m'
M = '\033[35m'
B = '\033[1m'

def print(msg='', end='\n'):
    sys.stdout.write(msg + end)
    sys.stdout.flush()
