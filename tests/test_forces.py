#!/usr/bin/env python

import sys
sys.path.insert(0, '')
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import graphtec
import backend

offset = (5,1)
matrix = (1,0,0,1)

g = backend.make()  # --cameo for Cameo 3/4/5 and Cameo 5 Alpha; see backend.py

g.start()

g.set(offset=offset, matrix=matrix)
g.set(speed=2)

for i in range(0,6):
  for j in range(0,5):
    g.set(force=1+j+5*i)
    tx = 0.5*j
    ty = 0.5*i
    g.closed_path([(tx,ty),(tx+0.3,ty),(tx+0.3,ty+0.3),(tx,ty+0.3)])

g.end()
