#!/bin/bash

# Pull Current Branch
git pull origin $(git branch --show-current)

# Set permissions for www-data
chown -R www-data:www-data ./

# Show the git status
git status