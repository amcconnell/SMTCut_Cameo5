#!/usr/bin/env python

import sys
sys.path.insert(0, '')
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import random
import graphtec
import backend

def pads1(g):
  for i in range(0,20):
    for j in range(0,20):
      g.path([(0,0),(0.05,0)])
      x = 0.05+0.016*i
      y = 0.05+0.016*j
      g.path([(x,y),(x+0.008,y)])

g = backend.make()  # --cameo for Cameo 3/4/5 and Cameo 5 Alpha; see backend.py

g.start()

g.set(offset=(1.75,1.75))
g.set(speed=1)
g.set(force=1)

pads1(g)

g.end()
