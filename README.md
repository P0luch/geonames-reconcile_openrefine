# GeoNames — OpenRefine Reconciliation

> **Prototype** — ce projet est issu d'une session de vibe coding assistée par IA. Il n'a pas vocation à être utilisé en production sans revue du code.

Service de réconciliation [GeoNames](https://www.geonames.org/) pour [OpenRefine](https://openrefine.org/).

## Fonctionnalités

- Réconciliation de lieux géographiques via l'API GeoNames
- Preview des résultats : nom, pays, coordonnées, carte [OpenStreetMap](https://www.openstreetmap.org/) générée côté serveur via [staticmap](https://github.com/komoot/staticmap)
- Extension de données : latitude, longitude, nom localisé, nom officiel, code pays, hiérarchie administrative (Adm1 à Adm5), Wikipedia, population...
- Preview : chemin hiérarchique complet et carte OSM avec zoom adaptatif
- Interface de configuration : username, langue de recherche, langue des résultats, nombre de résultats, tolérance orthographique
- Notifications navigateur en cas de dépassement de quota GeoNames

### Scoring

Les candidats retournés par GeoNames sont scorés par le service sur une échelle de 0 à 100 en comparant la requête aux noms disponibles dans la notice (nom principal, nom localisé, noms alternatifs). Par défaut le seuil est configuré à 40 et modifiable depuis l'interace de configuration.

| Score | Condition |
|---|---|
| 100 | Correspondance exacte sur le nom principal ou le nom localisé |
| 90 | Correspondance exacte sur un nom alternatif |
| 75 | Les mots de la requête sont tous inclus dans le nom principal |
| 65 | Les mots de la requête sont tous inclus dans un nom alternatif |
| 1–64 | Coefficient de Dice sur les bigrammes (similarité partielle) |
| — | Candidat écarté si score < seuil |

## Prérequis

- Python 3.8+
- Un compte GeoNames avec les web services activés
  - Créer un compte : https://www.geonames.org/login
  - Activer les services : **Manage Account › Free Web Services**

## Installation

```bash
# Cloner ou télécharger le dépôt
git clone https://github.com/votre-compte/geonames-openrefine-python.git
cd geonames-openrefine-python

# Créer et activer un environnement virtuel
python -m venv venv
source venv/bin/activate        # macOS / Linux
venv\Scripts\activate           # Windows

# Installer les dépendances
pip install -r requirements.txt

# Lancer le serveur
python app.py
```



## Configuration


| Paramètre | Description |
|---|---|
| **Nom d'utilisateur** | Votre identifiant GeoNames (obligatoire) |
| **Langue de recherche** | Langue dans laquelle sont rédigés vos termes à réconcilier |
| **Langue des résultats** | Langue dans laquelle GeoNames renvoie les noms de lieux |
| **Nombre de résultats** | Nombre de candidats renvoyés par requête (1–100, défaut : 8) |
| **Tolérance orthographique** | Niveau de correspondance approximative (0 = exacte, 1 = très souple, défaut : 0.8) |
| **Seuil de score** | Score minimum (0–100) en dessous duquel un candidat est écarté (défaut : 40) |


> **Important** : lors du premier lancement, autorisez les notifications navigateur. Vous serez alerté en cas de dépassement de crédits GeoNames même si l'onglet est en arrière-plan.

## Utilisation dans OpenRefine

1. Lancer le serveur
2. Dans OpenRefine, sélectionner une colonne › **Reconcile › Start reconciling**
3. Cliquer sur **Add Standard Service** et entrer l'URL :
   ```
   http://localhost:5065/reconcile
   ```
4. Sélectionner le type et lancer la réconciliation :
   - **Place search** — recherche par nom de lieu
   - **GeoNames ID** — réconciliation directe par identifiant numérique GeoNames 

## Crédits API GeoNames

Chaque requête de réconciliation consomme 1 crédit. En cas de dépassement, le service interrompt les appels et vous notifie via l'interface et les notifications navigateur. Le traitement doit être interrompu manuellement depuis OpenRefine, ce qui entraînera la perte des réconciliations, ou peut continuer mais aucun candidat ne sera renvoyé.
Plus d'informations : https://www.geonames.org/export/credits.html

Les données sont mises en cache lors de la réconciliation et enregistrées sur le disque dans deux fichiers : `record_cache.pkl` (notices GeoNames complètes) et `search_cache.pkl` (requêtes → identifiants). Le cache est rechargé automatiquement au démarrage du serveur — il n'est pas nécessaire de refaire une réconciliation pour accéder aux données d'une session précédente. Il est possible d'exporter et d'importer le cache sous forme d'archive ZIP depuis l'interface de configuration.

> Pour créer des lots à réconcilier, ajoutez une colonne d'index (`Edit column > Add index column ou › Add column based on this column` → `row.index`) puis filtrez par tranches avec une facette numérique. Cela permet de traiter de reprendre facilement en cas de dépassement de quota. 

Il est recommandé de vider le cache entre deux projets de réconciliation distincts, pour prévenir des potentiels conflits de données.