# Diagnostics SmartPI

SmartPI expose un capteur de diagnostic Home Assistant dédié. Son état indique
la phase de régulation courante et ses attributs suivent le schéma version 2.

## Enveloppe des attributs

```yaml
schema_version: 2
live:     # Diagnostics live complets
history:  # Contrat stable des séries temporelles
```

`live` est la source des dashboards, des cartes Markdown et de l'analyse
interactive. Son contenu est identique en mode normal et en mode debug.
`history` contient uniquement les valeurs destinées aux graphiques historiques.

Il n'existe pas de bloc d'attribut `debug` séparé.

## Profils Recorder

Le mode normal exclut l'attribut `live` complet du Recorder Home Assistant.
Seuls `schema_version`, l'état du capteur et `history` sont conservés.

Le mode debug enregistre `live` et `history`. Le contenu n'est pas dupliqué
dans `history` : le contrat historique reste identique dans les deux modes. Le
mode debug change donc la conservation, pas la publication.

Le bloc `history` contient ces huit séries :

| Chemin sous `history` | Signification |
|---|---|
| `temperature.indoor` | Température intérieure |
| `setpoint.filtered_setpoint` | Consigne filtrée utilisée par la régulation |
| `power.applied_percent` | Puissance physiquement appliquée |
| `power.command_percent` | Commande demandée avant les effets de l'actionneur |
| `power.pi_percent` | Contribution PI |
| `power.ff_percent` | Contribution feed-forward |
| `model.a` | Gain de chauffe appris |
| `model.b` | Coefficient de déperdition appris |

## Diagnostics live

Le bloc `live` est organisé par responsabilité :

| Bloc | Contenu |
|---|---|
| `control` | Phase, mode de régulation, gains, saturation et bandes de régulation |
| `power` | Puissances du cycle courant et suivant, répartition PI/FF, limites et puissance appliquée |
| `temperature` | Températures intérieure/extérieure, erreurs et état de l'intégrale |
| `model` | Modèle A/B, confiance, constante de temps et temps morts |
| `learning` | Étape d'apprentissage, progression du bootstrap, mises à jour acceptées et état de dérive |
| `governance` | Régime thermique et décision de mise à jour du modèle |
| `feedforward` | État FF3, source de bande morte et diagnostics FFTrim canoniques |
| `setpoint` | Consigne filtrée, trajectoire, boost et résumé d'atterrissage |
| `autocalib` | État du superviseur de calibration automatique |
| `calibration` | État, nombre d'essais et date de la dernière calibration |
| `analysis` | Champs avancés utilisés par les cartes de diagnostic fournies |
| `dependencies` | Seulement avec des dépendances entre zones : le modèle composé, le verrou d'apprentissage, la réaction à la fenêtre, les voisins et la zone à deux sources de chaleur |
| `water` | Seulement pour un type de chauffage à eau : le facteur d'eau, d'où il vient, les maintiens, et la source de chaque signal de l'eau |
| `boiler` | Seulement pour un type de chauffage à eau avec une source de l'appel de la chaudière choisie : la source, l'appel et le mode d'apprentissage qui en découle |

`analysis` regroupe les valeurs live avancées dans `control`, `learning`,
`trajectory`, `landing`, `deadtime`, `governance`, `feedforward` et, lorsqu'il
est disponible, `twin`.

FFTrim utilise des blocs canoniques imbriqués :

```yaml
live:
  feedforward:
    fftrim:
      stationary: {}
      periodic: {}
      transfer: {}
      command_ownership: {}
      observation_mode: ...
      last_reject_reason: ...
      last_update_reason: ...
      last_result: ...
      last_transaction: ...
      windows_since_update: ...
```

### `dependencies`

Quand un thermostat a des dépendances entre zones (des zones voisines, ou une zone à deux sources
de chaleur), `live` reçoit un bloc `dependencies`. Il contient le modèle composé (`b_eff`,
`t_ref`), le verrou d'apprentissage, la réaction à la fenêtre appliquée, et une entrée par voisin
avec son échange de chaleur appris. Pour la source principale d'une zone à deux sources de chaleur,
il contient aussi un bloc `second_source` : la commande et la chaleur de la source additionnelle,
son plafond de puissance, le renfort de rattrapage et l'ajustement de la zone. Un thermostat qui
sert de source additionnelle à un autre thermostat ne publie que son rôle, la source principale et
la commande qu'il applique. Sans dépendances, le bloc est absent. Ses champs sont listés dans
[Dépendances entre zones, §9](zone_dependencies.md#9-diagnostics).

### `water`

Pour un thermostat avec un type de chauffage à eau, `live` reçoit un bloc `water`. Il contient le
facteur d'eau appliqué, d'où il vient, le type de chauffage, la température de départ de l'eau
moyennée, les dernières raisons données par la source des données d'eau, le maintien pendant l'eau
chaude sanitaire (`no_heat`), le maintien de l'intégrale quand l'eau chauffe peu (`low_heat`),
l'indicateur « eau trop froide », et `inputs` : pour chaque signal de l'eau, sa source (une entité,
un fournisseur, ou aucune) et s'il donne des données en ce moment. Avec des données d'eau, les
valeurs `power` sont des puissances pour l'eau de référence, tandis que `current_cycle_percent` et
`next_cycle_percent` sont la commande envoyée à l'émetteur. Ses champs sont listés dans
[Entrées eau et chaudière, §8](water_input.md#8-diagnostics).

### `boiler`

Pour un type de chauffage à eau avec une source de l'appel de la chaudière choisie dans ses options
(la chaudière centrale de Versatile Thermostat, des entités ou un fournisseur), `live` reçoit un
bloc `boiler`, même quand la source ne donne pas de données. Il contient la source, si la chaudière
est appelée et, sinon, pourquoi, la façon dont l'apprentissage traite cette situation
(`emitter_cooling`, `held`, `emitter_warming`), la demande comparée aux seuils de démarrage de la
chaudière là où la source la donne, et `inputs` : d'où vient chaque signal. Ses champs sont listés
dans [Entrées eau et chaudière, §8](water_input.md#8-diagnostics) et expliqués dans
[§4](water_input.md#4-une-chaudière-qui-nest-pas-appelée).

### `humidity`

Quand Versatile Thermostat donne l'humidité de la pièce, `live` reçoit un bloc `humidity` dès
qu'une lecture a été vue. Il contient l'humidité (`humidity`), l'état d'un événement d'humidité
(`idle`, `event`, `settling`), la base (`baseline`) et le pic (`peak`) de l'événement, et `events`,
le nombre d'événements depuis le démarrage. Pendant un événement et sa stabilisation,
`learning_gate` vaut `humidity_event`. Ils sont expliqués dans
[la documentation technique, §4.7](technical_doc.md#47-événements-dhumidité).

## Fréquence de publication

Le capteur de diagnostic est rafraîchi lors des entrées de contrôle
significatives, des limites de cycle, des calculs explicitement forcés et des
services de diagnostic. Le timer interne de recalcul de 60 secondes ne publie
pas lorsque ses entrées et la puissance engagée sont inchangées. Le capteur
supprime également une écriture lorsque son état et l'enveloppe complète de ses
attributs sont identiques à la publication précédente.

Cela limite les écritures dans la machine d'état et le Recorder sans modifier
la temporisation de contrôle de SmartPI.

## Chemins pour les consommateurs

Les consommateurs live doivent lire `attributes.live`. Les graphiques
historiques doivent lire `attributes.history`. Les consommateurs doivent
vérifier `attributes.schema_version == 2` avant d'interpréter la structure imbriquée.
