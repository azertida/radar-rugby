name: Mise à jour quotidienne du Radar Rugby

on:
  schedule:
    - cron: '25 5 * * *'
    - cron: '53 8 * * *'
  workflow_dispatch:

permissions:
  contents: write

jobs:
  build:
    runs-on: ubuntu-latest
    
    steps:
      - name: Cloner le dépôt
        uses: actions/checkout@v4
      
      - name: Configurer Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      
      - name: Récupérer la grille Pickx (nouvelle API tv.pickx.be)
        # S'arrête en erreur si la grille est vide : rien n'est publié,
        # l'appli garde les données de la veille.
        run: |
          python3 pickx_grab.py --channels=pickx_channels.xml --days=7 --output=/tmp/pickx_guide.xml
      
      - name: Lancer le radar
        run: |
          python3 radar.py --source=/tmp/pickx_guide.xml
      
      - name: Commit le radar.json si modifié
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add radar.json
          if git diff --staged --quiet; then
            echo "Aucune modification à committer, sortie propre"
            exit 0
          fi
          git commit -m "🏉 Mise à jour automatique du Radar Rugby"
          git pull --rebase origin main
          git push
