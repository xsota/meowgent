### Update requirements.txt
```
python3 -m pip freeze > requirements.txt
```

### Deploy
Every push to master will deploy a new version of this app. Deploys happen automatically.


#### Manually deploy
```
git push heroku master
```
