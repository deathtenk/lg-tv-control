#!/bin/bash

docker build \
  --platform linux/amd64 \
  -t lg-tv-control-builder .

docker create --name lg-tv-build lg-tv-control-builder

mkdir -p ./dist

docker cp \
  lg-tv-build:/app/dist/lg-tv-control \
  ./dist/lg-tv-control-linux-x86_64

docker rm lg-tv-build
