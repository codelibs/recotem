#!/bin/bash
CURRENT_DIR=`pwd`
cd recotem/api && rm -rf migrations && \
   mkdir migrations && cd migrations && touch __init__.py && cd $CURRENT_DIR
python manage.py makemigrations
sd "0009_auto_\d+_\d+" "0008_chordcounter" $(fd ".*py$" recotem/api/migrations)
