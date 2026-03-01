# FoetoPath — Slide Viewer

Serveur Flask léger pour visionner des lames numériques (MRXS, SVS, NDPI, TIFF, SCN...) via OpenSlide + OpenSeadragon.

## Installation rapide

```bash
pip install -r requirements.txt
python app.py
```

Ou double-cliquez sur `start.bat` (Windows) / `start.sh` (Linux/Mac).

## Utilisation

1. Lancez le serveur → le navigateur s'ouvre sur `http://127.0.0.1:5000`
2. Entrez le chemin du dossier racine contenant vos lames
3. Cliquez sur un cas (sous-dossier) dans la sidebar gauche
4. La première lame s'affiche, les autres sont en preview cliquable dans le bandeau du bas

## Raccourcis clavier

| Touche | Action |
|--------|--------|
| `←` `→` | Lame précédente / suivante |
| `R` | Reset zoom |
| `F` | Plein écran |
| `Enter` | Charger le dossier (dans la barre de saisie) |

## Options de lancement

```bash
python app.py --port 8080              # Port personnalisé
python app.py --root "D:\Lames\2024"   # Dossier pré-rempli
python app.py --host 0.0.0.0           # Accessible sur le réseau local
python app.py --debug                  # Mode debug Flask
```

## Structure attendue des dossiers

```
Dossier racine/
├── Cas_001/
│   ├── lame_HE.mrxs
│   ├── lame_HE/           (données Mirax)
│   └── lame_CD34.mrxs
├── Cas_002/
│   └── placenta.mrxs
└── ...
```

## Formats supportés

MRXS (Mirax), SVS (Aperio), NDPI (Hamamatsu), TIFF/BigTIFF, SCN (Leica), BIF, VMS, VMU

## Dépendances

- Python 3.10+
- Flask
- openslide-python + openslide-bin
- Pillow
