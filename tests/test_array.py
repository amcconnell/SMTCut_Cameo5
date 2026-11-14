#!/usr/bin/env python

import sys
sys.path.insert(0, '')
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import random
import graphtec
import backend

def pads1(g):
  for i in range(0,20):
    g.path([(0,0),(0.07,0.07)])
    for j in range(0,20):
      x = 0.1*i
      y = 0.008*j
      g.path([(x,y),(x+0.05,y)])

def pads2(g):
  for i in range(0,20):
    g.path([(0,0),(0.07,0.07)])
    for j in range(0,20):
      x = 0.008*i
      y = 0.4+0.1*j
      g.path([(x,y),(x,y+0.05)])

g = backend.make()  # --cameo for Cameo 3/4/5 and Cameo 5 Alpha; see backend.py

g.start()

g.set(offset=(1,1))
g.set(speed=1)
g.set(force=1)

pads1(g)
pads2(g)

g.end()
