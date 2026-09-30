# SmartPI

- [SmartPI](#smartpi)
  - [Ce que fait SmartPI](#ce-que-fait-smartpi)
  - [Avant de commencer](#avant-de-commencer)
  - [Installation et mise en place](#installation-et-mise-en-place)
    - [Ajouter l'intégration SmartPI](#ajouter-lintégration-smartpi)
    - [Sélectionner SmartPI dans Versatile Thermostat](#sélectionner-smartpi-dans-versatile-thermostat)
      - [Configuration par thermostat](#configuration-par-thermostat)
  - [Vannes de radiateur et linéarisation](#vannes-de-radiateur-et-linéarisation)
    - [Pourquoi une vanne peut être difficile à réguler](#pourquoi-une-vanne-peut-être-difficile-à-réguler)
    - [Ce que fait la linéarisation](#ce-que-fait-la-linéarisation)
    - [Quand l'activer](#quand-lactiver)
    - [Choisir les valeurs](#choisir-les-valeurs)
  - [Phases de fonctionnement](#phases-de-fonctionnement)
    - [Phase d'apprentissage](#phase-dapprentissage)
      - [Comment le bootstrap chauffe](#comment-le-bootstrap-chauffe)
      - [Étape 1 — Mesure des temps morts](#étape-1--mesure-des-temps-morts)
      - [Étape 2 — Apprentissage des déperditions (`b`)](#étape-2--apprentissage-des-déperditions-b)
      - [Étape 3 — Apprentissage du gain de chauffe (`a`)](#étape-3--apprentissage-du-gain-de-chauffe-a)
      - [Sortie du bootstrap](#sortie-du-bootstrap)
    - [Phase stable](#phase-stable)
    - [Recalibration automatique](#recalibration-automatique)
  - [Réglages conseillés](#réglages-conseillés)
  - [Configuration](#configuration)
  - [Diagnostics et carte Markdown](#diagnostics-et-carte-markdown)
  - [Panel de régulation avec Equinox](#panel-de-régulation-avec-equinox)
  - [Services](#services)
    - [`set_smartpi_learning`](#set_smartpi_learning)
    - [`reset_smartpi_learning`](#reset_smartpi_learning)
    - [`force_smartpi_calibration`](#force_smartpi_calibration)
    - [`reset_smartpi_integral`](#reset_smartpi_integral)

## Ce que fait SmartPI

SmartPI est une alternative au TPI classique pour Versatile Thermostat.

Son objectif est simple : au lieu d'utiliser un comportement fixe, il apprend comment votre pièce chauffe et refroidit réellement, puis adapte automatiquement la régulation. Chaque VTherm SmartPI reste mono-mode : HEAT ou COOL, jamais les deux à la fois.

Concrètement, SmartPI apprend :

- avec quelle force l'équipement actif chauffe ou refroidit la pièce,
- à quelle vitesse l'extérieur réchauffe ou refroidit passivement la pièce,
- combien de temps la pièce met à réagir quand l'équipement démarre ou s'arrête.

À partir de là, SmartPI construit une commande de chauffage ou de
refroidissement généralement plus précise qu'un TPI fixe :

- il corrige l'écart actuel à la consigne,
- il estime la puissance nécessaire pour tenir la consigne,
- il applique des protections près de la consigne pour limiter les dépassements et les oscillations inutiles.

Il n'est pas nécessaire de connaître la théorie des boucles PI pour bien utiliser SmartPI. L'idée importante est surtout qu'il a besoin d'une première période d'apprentissage avant de réguler dans son mode normal.

## Avant de commencer

Pour que SmartPI apprenne correctement, le thermostat doit disposer :

- d'une température intérieure fiable,
- d'une température extérieure,
- d'assez de temps pour observer des phases normales de chauffe et de refroidissement.

Pendant la première phase d'apprentissage, essayez d'éviter :

- les fenêtres ouvertes longtemps,
- les gros changements de planning,
- les apports de chaleur inhabituels comme un fort ensoleillement, une cheminée ou beaucoup de présence,
- la modification de nombreux réglages SmartPI alors que l'apprentissage est encore en cours.

Deux conseils pratiques aident beaucoup :

- laissez SmartPI fonctionner sans interruption pendant le premier ou les deux premiers jours,
- en HEAT, utilisez une consigne suffisamment au-dessus de la température extérieure ; en COOL, une température extérieure suffisamment au-dessus de la consigne aide à rendre les réponses active et passive observables.

En pratique, l'apprentissage peut prendre de quelques heures à 48 heures sur les systèmes lents ou très inertiels.

## Installation et mise en place

Installez l'intégration via HACS (ou manuellement) comme décrit dans le [README](../../README.fr.md), puis redémarrez Home Assistant.

Deux étapes sont nécessaires après le redémarrage : ajouter l'intégration SmartPI dans Home Assistant, puis l'activer dans Versatile Thermostat.

### Ajouter l'intégration SmartPI

Allez dans **Paramètres → Intégrations → Ajouter une intégration**, puis recherchez *SmartPI*.

Lors de la première installation, SmartPI crée automatiquement l'entrée « SmartPI defaults » (l'entrée des valeurs par défaut). Elle contient les réglages partagés par les thermostats (marqués « partagé » dans le [tableau de configuration](#configuration)) et les signaux de la chaudière. Vous pouvez les modifier ensuite depuis **Paramètres → Intégrations → SmartPI → Configurer**. Si vous supprimez cette entrée, ajouter de nouveau l'intégration SmartPI la recrée avec les valeurs de départ, même quand des thermostats gardent leur propre entrée ; les thermostats qui utilisent les valeurs par défaut sont rechargés.

### Sélectionner SmartPI dans Versatile Thermostat

Une fois l'intégration SmartPI ajoutée, ouvrez la configuration de l'appareil Versatile Thermostat que vous souhaitez piloter avec SmartPI. Dans l'étape **Underlyings**, repérez le sélecteur d'algorithme et choisissez **SmartPI**.

![VT underlyings — sélection de l'algorithme SmartPI](../../assets/screens/config_algo.png)

Répétez cette étape pour chaque thermostat que vous souhaitez faire fonctionner avec SmartPI.

#### Configuration par thermostat

Une entrée propre est facultative. Un thermostat sur SmartPI qui n'en a pas prend les réglages partagés dans les valeurs par défaut. Il fonctionne sans les réglages propres à un thermostat : pas de type de chauffage, pas de délais d'activation, pas de courbe de vanne et pas de redémarrage du cycle PWM, comme dans la version 0.4.0.

Pour donner à un thermostat ses propres réglages, ouvrez la configuration de l'intégration SmartPI et ajoutez une nouvelle entrée de thermostat. Sélectionnez le thermostat cible dans la liste. Ses sections s'ouvrent ensuite dans un menu, comme dans ses options (ci-dessous) ; l'entrée est créée à **Save and finish** (enregistrer et terminer).

![SmartPI — configuration par thermostat](../../assets/screens/config_smartpi.png)

L'entrée d'un thermostat contient les réglages marqués « ce thermostat seulement » dans le [tableau de configuration](#configuration) et l'interrupteur **Use the SmartPI defaults** (utiliser les valeurs par défaut de SmartPI ; libellé affiché en anglais). Interrupteur activé, les réglages partagés viennent des valeurs par défaut, et un changement fait plus tard là-bas atteint ce thermostat. Interrupteur désactivé, l'entrée contient ses propres valeurs des réglages partagés ; elles partent d'une copie des valeurs par défaut. Un réglage que l'entrée ne définit pas n'est jamais pris dans les valeurs du code : un réglage partagé vient des valeurs par défaut, un réglage « ce thermostat seulement » n'est pas utilisé. Une entrée créée avant l'existence de l'interrupteur garde ses propres valeurs (interrupteur désactivé).

Les options fonctionnent comme celles de Versatile Thermostat. Chaque section s'ouvre depuis un menu et y revient. Vos modifications restent dans la fenêtre ouverte jusqu'au dernier élément, **Save and finish**, qui les écrit ; fermer la fenêtre les abandonne. Le menu liste les sections qui ont des modifications non enregistrées. Quand une modification touche ce que SmartPI a appris (un nouveau type de chauffage, d'autres données d'eau, la zone ou un voisin), **Save and finish** demande une seule fois s'il faut remettre à zéro le modèle appris, et en donne les raisons.

Les options de l'entrée « SmartPI defaults » (l'entrée des valeurs par défaut) ouvrent un menu (ses libellés sont affichés en anglais) avec : les réglages du régulateur (Regulator settings : les réglages partagés) ; les signaux de la chaudière (Boiler signals : des entités qui disent à SmartPI ce que fait la chaudière, partagées par chaque thermostat qui lit des entités) ; et **Save and finish**. Les options d'une entrée par thermostat ouvrent un menu, lui aussi en anglais, avec : **Heating type and regulation** (type de chauffage et régulation : le type de chauffage d'abord, puis les délais d'activation, le redémarrage du cycle PWM, pour une vanne l'interrupteur de la courbe de vanne, et **Use the SmartPI defaults** ; interrupteur désactivé, une deuxième étape contient les propres valeurs de ce thermostat pour les réglages partagés) ; pour un type de chauffage à eau, **Water and boiler** (eau et chaudière : d'où viennent les données d'eau et l'appel de la chaudière) ; la zone à deux sources de chaleur (Zone with two heat sources) ; les zones voisines (Neighbouring zones) ; pour une vanne linéarisée, la courbe de vanne (Valve curve) ; et **Save and finish**. Le menu suit vos choix : un type de chauffage à eau choisi dans la fenêtre fait apparaître **Water and boiler** tout de suite. Une zone à deux sources de chaleur est une pièce chauffée par deux thermostats SmartPI, par exemple un radiateur et une boucle de plancher chauffant. Elle se configure depuis le thermostat maître : le Versatile Thermostat dont les appareils sont ces deux thermostats. Les zones voisines (dépendances entre zones) sont les zones qui échangent de la chaleur avec celle-ci, par une porte ou en permanence. Un thermostat qui sert de source additionnelle à une zone ouvre à la place un menu à lui. Il montre où sa zone est configurée, et les réglages qui s'appliquent encore à lui : son type de chauffage, ses délais d'activation et la courbe de vanne (et, pour un type de chauffage à eau, ses données d'eau). La façon dont SmartPI apprend et utilise tout cela est décrite dans [Dépendances entre zones et deuxième source de chaleur](zone_dependencies.md).

Les réglages d'un thermostat commencent par son **type de chauffage** : non défini (par défaut), radiateur à eau, plancher chauffant à eau, tapis chauffant électrique ou radiateur électrique rapide. Non défini veut dire pas de données d'eau et pas d'appel de la chaudière, comme dans la version 0.4.0. Un type à eau utilise les données d'eau et l'appel de la chaudière venant des sources choisies dans **Water and boiler**. Les données d'eau sont la température de départ de l'eau (la température de l'eau envoyée dans le circuit du thermostat) et les signaux de la chaudière ; elles viennent d'entités Home Assistant (de n'importe quelle intégration, ou d'un capteur sur le tuyau de départ) ou d'un fournisseur (une intégration qui implémente le contrat de SmartPI). L'appel de la chaudière dit si la chaudière reçoit l'ordre de chauffer ; il vient de la chaudière centrale de Versatile Thermostat (par défaut là où elle sert le thermostat), d'entités ou d'un fournisseur. Sans données d'eau, la commande est la même qu'avant. Tant qu'une zone demande une chaleur qui ne vient pas, SmartPI ne compte pas sa commande comme de la chaleur dans son apprentissage. Choisissez un type électrique pour un émetteur électrique : il ne lit ni l'un ni l'autre. Voir [Entrées eau et chaudière](water_input.md) et sa [section 4](water_input.md#4-une-chaudière-qui-nest-pas-appelée).

Il vaut mieux configurer une zone voisine dans les entrées des deux zones, pour que chacune voie l'autre. Quand la fenêtre d'un voisin est ouverte, SmartPI applique la réaction choisie dans sa propre régulation. Le Versatile Thermostat de la zone continue d'afficher son propre état de fenêtre, son mode et son preset, et les diagnostics montrent la réaction en vigueur.

## Vannes de radiateur et linéarisation

Cette section concerne uniquement les thermostats qui pilotent directement une vanne, par exemple une tête thermostatique de radiateur.

### Pourquoi une vanne peut être difficile à réguler

Une vanne de radiateur ne se comporte pas toujours comme un chauffage électrique.

Avec un chauffage électrique, demander `40%` produit généralement environ deux fois plus de chaleur que demander `20%`. Avec beaucoup de vannes de radiateur, ce n'est pas aussi régulier : les premiers pourcents peuvent ne presque rien faire, puis une petite ouverture supplémentaire peut laisser passer beaucoup d'eau chaude.

Sur certaines installations, une vanne peut déjà laisser passer la majorité du débit alors qu'elle n'est ouverte qu'à environ `20%`. Ensuite, ouvrir davantage change beaucoup moins le débit.

En pratique, cela peut donner ce type de sensation :

| Commande envoyée à la vanne | Effet possible sur le radiateur                                   |
| --------------------------- | ----------------------------------------------------------------- |
| `0%` à quelques pourcents   | pas de chaleur visible                                            |
| petite ouverture            | le radiateur commence seulement à chauffer                        |
| environ `15%` à `25%`       | grande partie du débit déjà présente                              |
| au-delà                     | changement plus faible, parfois surtout plus de bruit hydraulique |

Les valeurs exactes dépendent de la vanne, du corps de vanne, de l'équilibrage du radiateur et de l'installation hydraulique. Il faut donc éviter de chercher une valeur parfaite au premier essai.

### Ce que fait la linéarisation

La linéarisation de courbe de vanne sert à traduire la demande SmartPI en position de vanne.

SmartPI continue de raisonner en demande de chauffe simple :

- `0%` signifie pas de chauffe,
- `50%` signifie une demande moyenne,
- `100%` signifie une demande maximale.

La linéarisation transforme ensuite cette demande en ouverture de vanne plus adaptée au comportement réel du radiateur. L'objectif est d'utiliser plus finement la petite zone où la vanne change réellement le débit, au lieu d'envoyer directement la demande brute à la vanne.

Exemple simplifié :

| Demande SmartPI | Ouverture envoyée à la vanne         |
| --------------- | ------------------------------------ |
| `0%`            | `0%`                                 |
| faible demande  | autour de l'ouverture minimale utile |
| `80%`           | autour de l'ouverture au coude       |
| `100%`          | ouverture maximale autorisée         |

Cette correction ne remplace pas l'apprentissage SmartPI. Elle aide seulement SmartPI à parler plus naturellement à une vanne non linéaire.

### Quand l'activer

Activez cette option si :

- votre thermostat VTherm est de type vanne,
- SmartPI pilote directement une ouverture de vanne,
- le radiateur semble passer rapidement de froid à très chaud avec seulement quelques pourcents d'ouverture,
- les petites variations de commande donnent des réactions trop fortes ou trop irrégulières.

L'option est proposée uniquement pour les thermostats qui exposent une commande de vanne.

### Choisir les valeurs

Les valeurs par défaut donnent un point de départ raisonnable. Ajustez-les seulement si vous avez observé le comportement de votre radiateur.

| Paramètre                       | À quoi il sert                                                                                     | Valeur de départ |
| ------------------------------- | -------------------------------------------------------------------------------------------------- | ---------------- |
| **Ouverture minimale de vanne** | Première ouverture où le radiateur commence réellement à chauffer.                                 | `7%`             |
| **Demande au coude**            | Demande SmartPI à partir de laquelle on considère que la vanne arrive dans sa zone de débit élevé. | `80%`            |
| **Ouverture de vanne au coude** | Position physique de la vanne à cette demande.                                                     | `15%`            |
| **Ouverture maximale de vanne** | Ouverture maximale autorisée.                                                                      | `100%`           |

Pour trouver l'ouverture minimale, le plus simple est d'observer le radiateur :

1. Laissez le radiateur refroidir.
2. Lancez une demande de chauffe.
3. Augmentez doucement l'ouverture de la vanne, par petits pas.
4. Attendez au moins une minute entre deux essais.
5. Notez la première valeur où le tuyau ou le radiateur commence vraiment à chauffer.

Si vous n'avez pas envie de tester finement, gardez les valeurs par défaut. Si votre vanne semble très rapide, une ouverture au coude autour de `20%` à `25%` peut être un essai raisonnable. Si le radiateur devient bruyant à pleine ouverture, baissez l'ouverture maximale.

Après un changement, laissez SmartPI fonctionner plusieurs cycles avant de juger le résultat. Une seule chauffe ne suffit pas toujours à conclure.

## Phases de fonctionnement

### Phase d'apprentissage

SmartPI démarre dans une phase de bootstrap. Pendant cette phase, il utilise une stratégie de chauffe simple pour observer le comportement de la pièce, puis en extrait les paramètres physiques dont il a besoin pour réguler correctement.

#### Comment le bootstrap chauffe

Pendant le bootstrap, SmartPI ne cherche pas à maintenir la température avec précision. Il alterne entre chauffe complète et arrêt complet pour observer des réponses thermiques nettes :

- la chauffe démarre quand la température descend sous `consigne - 0.3°C`,
- la chauffe s'arrête quand la température monte au-dessus de `consigne + 0.5°C`.

Cela produit des oscillations de température visibles, qui sont attendues et volontaires à ce stade.

#### Étape 1 — Mesure des temps morts

La première chose que SmartPI doit apprendre est **le temps de réaction de la pièce** quand la chauffe démarre ou s'arrête. Ces délais s'appellent les *temps morts* :

- **temps mort de chauffe** : le délai entre le moment où SmartPI envoie une commande de chauffe et le moment où la température intérieure commence à monter,
- **temps mort de refroidissement** : le délai entre l'arrêt de la chauffe et le moment où la température commence à baisser.

Les temps morts dépendent du type d'émetteur, de la taille de la pièce et du placement du capteur. Ils doivent être mesurés avant que SmartPI puisse interpréter correctement les observations de chauffe et de refroidissement.

#### Étape 2 — Apprentissage des déperditions (`b`)

Une fois les temps morts considérés comme fiables, SmartPI commence à collecter des **observations de refroidissement** : il mesure à quelle vitesse la pièce perd sa chaleur quand le chauffage est éteint.

À partir de ces observations, il calcule `b`, le coefficient de déperdition thermique. Ce paramètre représente la vitesse à laquelle la pièce se refroidit en fonction de la différence entre température intérieure et extérieure.

SmartPI a besoin d'au moins 8 observations de refroidissement valides avant que `b` soit considéré comme utilisable.

#### Étape 3 — Apprentissage du gain de chauffe (`a`)

Une fois que `b` dispose de suffisamment d'observations pour être considéré comme fiable, SmartPI commence à collecter des **observations de chauffe** : il mesure à quelle vitesse la pièce se réchauffe pour une commande de chauffe donnée.

À partir de ces observations, il calcule `a`, le gain de chauffe. Ce paramètre représente l'efficacité avec laquelle votre système de chauffage fait monter la température intérieure.

SmartPI a besoin d'au moins 6 observations de chauffe valides pour `a`.

#### Sortie du bootstrap

SmartPI quitte le bootstrap et bascule en régulation normale une fois que `a` et `b` disposent de suffisamment d'observations pour publier un premier modèle thermique.

Les buffers d'observation continuent ensuite à se remplir jusqu'à 31 échantillons pendant la régulation normale, ce qui permet au modèle de continuer à se consolider après le bootstrap. La confiance complète reste plus stricte que la sortie de bootstrap — tant que le nombre d'observations n'est pas suffisant pour cette confiance complète, SmartPI régule avec le modèle publié tout en gardant certaines corrections internes gelées.

Ce qu'il faut retenir pendant le bootstrap :

- les oscillations de température sont normales et attendues,
- la régulation est volontairement simple à ce stade,
- les diagnostics sont particulièrement utiles pour suivre la progression,
- `model.confidence` indique `ab_bootstrap` jusqu'à ce que le premier modèle thermique soit disponible,
- la vitesse dépend de la qualité des observations réelles, pas seulement du temps écoulé.

Avec des dépendances entre zones, l'apprentissage se met en pause tant que la porte d'un voisin est ouverte et pendant 10 minutes après sa fermeture, et tant que la fenêtre d'un voisin est ouverte. Dans une zone à deux sources de chaleur, SmartPI part plutôt de l'ajustement commun de la zone (un seul modèle ajusté pour les deux sources ensemble) ; voir [Dépendances entre zones, §5.5](zone_dependencies.md#55-apprentissage).

### Phase stable

Quand le modèle thermique devient fiable, SmartPI bascule dans son mode normal de régulation.

À ce moment-là, SmartPI :

- calcule automatiquement ses gains PI à partir du modèle appris,
- ajoute une part anticipative de maintien basée sur les conditions de la pièce et de l'extérieur,
- adapte son comportement près de la consigne avec une deadband et des protections supplémentaires.

Près de la température cible, SmartPI cherche à éviter les micro-corrections permanentes. Le résultat attendu est une régulation plus stable, avec moins de corrections inutiles qu'un TPI fixe.

Lors d'une hausse de consigne en chauffage, le filtre de consigne utilise aussi le modèle appris pour gérer l'approche finale de la cible. La branche proportionnelle suit une référence filtrée, tandis que la consigne brute reste disponible pour la branche intégrale. Près de la cible, SmartPI peut plafonner la demande de chauffe interne quand le modèle prédit que la chaleur déjà injectée suffit à atteindre la consigne. Cet atterrissage aide la pièce à ralentir avant la cible au lieu de continuer à chauffer uniquement parce que le feed-forward ou l'état PI gelé reste positif.

Avec `FF3`, activé par défaut, SmartPI peut aussi appliquer une petite correction prédictive près de la consigne lorsqu'il détecte un contexte crédible de perturbation externe.

### Recalibration automatique

SmartPI continue de surveiller la qualité de son modèle dans le temps.

Si la qualité de l'apprentissage ne s'améliore plus assez, il peut déclencher automatiquement une séquence de recalibration pour rafraîchir le modèle et les temps morts.

Points utiles à connaître :

- un snapshot de référence est mémorisé quand le modèle devient fiable,
- un snapshot roulant est rafraîchi ensuite,
- si le temps mort de refroidissement ne peut pas être appris pendant longtemps, SmartPI peut continuer avec un snapshot partiel,
- après plusieurs échecs de recalibration, SmartPI continue de fonctionner et signale un modèle dégradé dans les diagnostics.

### Douches et cuisson

Quand Versatile Thermostat a un capteur d'humidité pour la pièce, SmartPI l'utilise sans aucun
réglage : une montée rapide de l'humidité (une douche, une cuisson) signale une chaleur qui ne
vient pas du chauffage. Tant qu'elle dure et un moment après, SmartPI n'apprend pas de la pièce et
ne laisse pas cette chaleur baisser son besoin de chauffage. Détails :
[documentation technique, §4.7](technical_doc.md#47-événements-dhumidité).

## Réglages conseillés

Les réglages par défaut conviennent dans la plupart des installations.

Pour démarrer simplement :

- gardez les seuils d'hystérésis par défaut,
- laissez `FF3` activé sauf raison précise de le désactiver,
- laissez le filtre de consigne activé par défaut,
- ajustez d'abord la deadband si la température oscille trop autour de la cible.

Évitez de modifier plusieurs paramètres à la fois pendant la première phase d'apprentissage. Il vaut mieux laisser SmartPI terminer un cycle d'apprentissage propre, puis ne changer que ce qui est réellement nécessaire.

## Configuration

La colonne « Où il se règle » dit à qui appartient chaque réglage. **Partagé** : dans l'entrée « SmartPI defaults » ; l'entrée d'un thermostat ne contient sa propre valeur que si son interrupteur **Use the SmartPI defaults** est désactivé. **Ce thermostat seulement** : dans l'entrée propre du thermostat ; non défini, il n'est pas utilisé.

| Paramètre | Rôle | Où il se règle | Valeur par défaut |
| --------- | ---- | -------------- | ----------------- |
| **Heating type** (type de chauffage, libellé affiché en anglais) | Non défini, radiateur à eau, plancher chauffant à eau, tapis chauffant électrique ou radiateur électrique rapide ; un type à eau utilise les données d'eau et l'appel de la chaudière venant des sources choisies dans Water and boiler ([Entrées eau et chaudière, §2](water_input.md#2-configuration)). | ce thermostat seulement | non défini |
| **Délai minimal d'activation** | Durée minimale pendant laquelle le chauffage reste allumé une fois activé. | ce thermostat seulement | `0 s` |
| **Délai minimal de désactivation** | Durée minimale pendant laquelle le chauffage reste éteint une fois désactivé. | ce thermostat seulement | `0 s` |
| **Autoriser le redémarrage PWM aux transitions de bande** | Permet à SmartPI de redémarrer le cycle PWM courant lors d'une entrée ou sortie de deadband ou de near-band. | ce thermostat seulement | `désactivé` |
| **Activer la linéarisation de courbe de vanne** | Adapte la demande SmartPI aux vannes de radiateur non linéaires (affiché pour une vanne). | ce thermostat seulement | `désactivé` |
| **Ouverture minimale de vanne** | Première ouverture utile lorsque la linéarisation est activée. | ce thermostat seulement | `7%` |
| **Demande au coude** | Demande SmartPI correspondant au changement de pente de la vanne. | ce thermostat seulement | `80%` |
| **Ouverture de vanne au coude** | Ouverture physique de la vanne au changement de pente. | ce thermostat seulement | `15%` |
| **Ouverture maximale de vanne** | Ouverture maximale autorisée lorsque la linéarisation est activée. | ce thermostat seulement | `100%` |
| **Use the SmartPI defaults** (utiliser les valeurs par défaut de SmartPI, libellé affiché en anglais) | Activé : les réglages partagés viennent de l'entrée « SmartPI defaults ». Désactivé : cette entrée contient ses propres valeurs. | ce thermostat seulement | `activé` (une entrée créée avant l'interrupteur : `désactivé`) |
| **Bande morte** | Zone de tolérance autour de la consigne (deadband). | partagé | `0.05°C` |
| **Hystérésis marche** | Seuil de redémarrage pendant le bootstrap. | partagé | `0.3°C` |
| **Hystérésis arrêt** | Seuil d'arrêt pendant le bootstrap. | partagé | `0.5°C` |
| **Facteur tau de relâchement** | Échelle du délai de relâchement intégral par rapport à la constante de temps apprise. | partagé | `0.5` |
| **Utiliser le filtre de consigne** | Active le lissage de consigne proportionnel et l'atterrissage de chauffe près de la cible. | partagé | `activé` |
| **Utiliser FF3** | Active une correction prédictive de court horizon près de la consigne, pendant la récupération après une perturbation. | partagé | `activé` |
| **Autoriser P amorti près du bord de bande morte** | Dans la bande morte, garde une action proportionnelle réduite près de son bord. Désactivée, la branche proportionnelle est coupée dans toute la bande morte. | partagé | `désactivé` |
| **Retour après un abaissement** | Zone à deux sources de chaleur : comment elle revient à sa consigne après une consigne plus basse. Rapide : pendant l'abaissement, la source lente reste chaude ; la zone revient plus tôt, avec environ 1 à 2 % d'énergie en plus. Économique : l'abaissement le plus profond et le moins d'énergie ; la zone met plus de temps à revenir. Chaque zone peut la remplacer ([Dépendances entre zones, §5.6](zone_dependencies.md#56-boucle-dallocation-et-freinage-anticipé)). | partagé | `Rapide` |
| **Enregistrer les diagnostics complets (debug)** | Enregistre les diagnostics live complets pour l'analyse historique. | partagé | `désactivé` |

Les délais d'activation sont ceux de SmartPI. Versatile Thermostat garde des délais du même nom dans ses réglages TPI ; pour un thermostat sur SmartPI, ils ne sont pas utilisés.

L'élément **Water and boiler** des options d'un thermostat, et l'élément **Boiler signals** de l'entrée « SmartPI defaults » ([Entrées eau et chaudière, §2](water_input.md#2-configuration)) ; leurs libellés sont affichés en anglais :

| Champ | Rôle | Valeur par défaut |
| ----- | ---- | ----------------- |
| **Water data** (données d'eau) | D'où viennent les données d'eau : aucune source, des entités Home Assistant, ou un fournisseur listé par son nom. | aucune |
| **Boiler's call** (appel de la chaudière) | D'où vient l'appel de la chaudière : aucune source, la chaudière centrale de Versatile Thermostat (proposée quand elle sert le thermostat), des entités Home Assistant, ou un fournisseur. | la chaudière centrale de Versatile Thermostat là où elle sert le thermostat, sinon aucune |
| **Supply water temperature** (température de départ de l'eau) | Le capteur de température de l'eau qui arrive au circuit de ce thermostat (données d'eau venant d'entités). | — |
| **Boiler signals** (signaux de la chaudière) | Comme dans SmartPI defaults (As in SmartPI defaults), ou ceux de ce thermostat (This thermostat's own). | comme dans SmartPI defaults |
| **Boiler can heat** (la chaudière peut chauffer) | On tant que la chaudière peut chauffer les circuits en ce moment. | — |
| **Hot water has priority** (l'eau chaude est prioritaire) | On tant que la chaudière chauffe l'eau chaude sanitaire au lieu des circuits. | — |
| **Boiler fault** (défaut de la chaudière) | On tant que la chaudière signale un défaut. | — |
| **Boiler is called for heat** (la chaudière est appelée pour chauffer) | On tant que la chaudière reçoit l'ordre de chauffer. | — |
| **Why the boiler is not called** (pourquoi la chaudière n'est pas appelée) | Un capteur texte : `below_threshold`, `no_demand`, `activation_delay` ou `boiler_fault`. | — |

Un changement des données d'eau ou du capteur de départ demande s'il faut remettre à zéro le modèle appris (recommandé), parce que la puissance apprise a alors d'autres unités.

### Redémarrage PWM aux transitions de bande

Lorsque cette option est désactivée, une transition de deadband ou de near-band met à jour la prochaine commande SmartPI mais n'interrompt pas le cycle PWM déjà en cours. C'est le comportement conseillé avec des cycles courts, car redémarrer trop souvent les cycles peut créer des commutations inutiles.

Activez cette option uniquement sur les systèmes thermiques à très forte inertie avec des cycles PWM longs, lorsque attendre la prochaine frontière de cycle retarde trop la réponse physique après une entrée ou une sortie de deadband ou de near-band.

## Diagnostics et carte Markdown

Pour le schéma des diagnostics et les profils Recorder, consultez le [Guide des Diagnostics SmartPI](./diagnostics.md).

Le capteur de diagnostic SmartPI publie une enveloppe versionnée. Les dashboards
et cartes Markdown lisent les diagnostics complets dans `attributes.live` ; les
graphiques historiques lisent le contrat stable de onze séries dans
`attributes.history`.

C'est l'endroit principal à consulter pour savoir :

- si SmartPI est encore en apprentissage ou déjà stable,
- si le modèle est considéré comme fiable,
- si une recalibration ou un mode dégradé a été signalé.

Le bloc live le plus utile pendant l'apprentissage est `live.learning`.

Champs importants :

- `stage` : état global comme `bootstrap`, `learning`, `monitoring` ou `degraded`,
- `bootstrap_progress_percent` : progression du bootstrap,
- `bootstrap_status` : étape bootstrap en cours,
- `accepted_updates_a` : mises à jour acceptées du modèle de chauffe,
- `accepted_updates_b` : mises à jour acceptées du modèle de déperdition,
- `history_target` : taille cible des buffers A/B,
- `last_reason` : dernière raison d'acceptation ou de rejet d'apprentissage.

Autres blocs utiles sous `live` :

- `control` : phase et mode de régulation courants,
- `power` : informations de commande du cycle courant et du suivant,
- `temperature` : température mesurée, erreur, état de l'intégrale,
- `model` : `a`, `b`, niveau de confiance et temps morts appris,
- `feedforward` : état du feed-forward et de FF3,
- `setpoint` : informations de consigne filtrée et d'atterrissage,
- `autocalib` : état de la supervision automatique,
- `calibration` : état d'une calibration forcée,
- `dependencies` : dépendances entre zones et zone à deux sources de chaleur, quand elles sont configurées ([Dépendances entre zones, §9](zone_dependencies.md#9-diagnostics)),
- `water` : les données d'eau, pour un type de chauffage à eau, et d'où vient chacun de leurs signaux ([Entrées eau et chaudière, §8](water_input.md#8-diagnostics)),
- `boiler` : si la chaudière est appelée, par quelle source, et comment l'apprentissage traite une zone qui demande de la chaleur et n'en reçoit pas ([Entrées eau et chaudière, §4](water_input.md#4-une-chaudière-qui-nest-pas-appelée)).

En mode normal, le bloc `setpoint` peut contenir :

- `filtered_setpoint` : référence suivie par la branche proportionnelle,
- `trajectory_active` : indique si une trajectoire de consigne est active,
- `trajectory_source` : indique pourquoi la trajectoire est active,
- `landing_active` : indique si l'atterrissage de chauffe est actif,
- `landing_reason` : raison de l'état d'atterrissage,
- `landing_u_cap` : cap de demande de chauffe interne appliqué pendant l'atterrissage,
- `landing_coast_required` : indique si SmartPI laisse la pièce en roue libre parce que le modèle prédit assez de chaleur stockée.

Les valeurs avancées utilisées par la carte fournie sont toujours disponibles
dans `live.analysis`. Le mode debug n'ajoute ni ne retire de champs publiés : il
autorise le Recorder Home Assistant à conserver le bloc `live` complet. En mode
normal, le Recorder conserve uniquement `history`, tandis que toutes les valeurs
live restent visibles.

Une carte Markdown Home Assistant est aussi disponible pour afficher plus simplement les diagnostics SmartPI dans le tableau de bord.

https://github.com/KipK/vtherm_smartpi/tree/master/cards

## Panel de régulation avec Equinox

SmartPI publie aussi le résumé `specific_states.smart_pi` et la référence d'entité `specific_states.regulation_diagnostics` sur l'entité thermostat. Tant que SmartPI utilise des données d'eau, il écrit aussi `specific_states.water_factor` : le facteur d'eau qu'il applique (la chaleur que donne l'émetteur avec l'eau actuelle, comparée à celle donnée avec une eau de référence). C'est la sortie de SmartPI pour toute intégration qui la lit ([Entrées eau et chaudière, §3.4](water_input.md#34-utilisation-dans-smartpi)). La [carte Lovelace Equinox](https://github.com/KipK/equinox) utilise ces attributs pour afficher un panel **Régulation** dédié depuis le menu de la carte climate.

Avec Equinox, vous pouvez suivre la progression d'apprentissage SmartPI, la confiance du modèle, le détail de puissance, l'atterrissage de consigne, les graphes d'historique et les actions de maintenance disponibles sans copier la carte Markdown de diagnostics.

![Vue d'ensemble du panel de régulation SmartPI dans Equinox](https://github.com/KipK/equinox/raw/master/assets/screens/regul-overview.png)

![Section apprentissage SmartPI dans Equinox](https://github.com/KipK/equinox/raw/master/assets/screens/regul-learning.png)

Installez Equinox depuis son dépôt ou HACS, configurez-le sur la même entité climate Versatile Thermostat et laissez `additional_dashboards` à sa valeur par défaut `auto`. Equinox détecte SmartPI automatiquement lorsque les diagnostics publiés sont disponibles.

## Services

SmartPI expose quatre services dans le domaine `vtherm_smartpi`. Chacun vise des entités climate de Versatile Thermostat, choisies comme cible du service ou données dans `entity_id` de ses données. Les thermostats sur un autre algorithme sont ignorés ; quand aucune des entités visées n'est un thermostat sur SmartPI, l'appel échoue avec une erreur affichée dans Home Assistant.

### `set_smartpi_learning`

Utilisez ce service pour mettre en pause ou reprendre l'apprentissage thermique d'un thermostat SmartPI. Réglez le champ obligatoire `learning_enabled` sur `false` pour suspendre l'apprentissage ou sur `true` pour le reprendre.

Pendant la pause, la régulation continue avec le modèle déjà appris, mais SmartPI ne collecte plus d'observations A/B ni de temps mort. La pause n'efface ni les paramètres appris ni l'historique des temps morts. SmartPI repart avec des fenêtres d'observation vierges à chaque changement de ce réglage, qui est conservé après un redémarrage de Home Assistant.

Le réglage courant est disponible dans `specific_states.smartpi_learning_enabled` sur l'entité climate et dans `live.learning.enabled` dans les attributs du capteur de diagnostic.

### `reset_smartpi_learning`

À utiliser quand le comportement thermique de la pièce a changé de manière importante, par exemple après des travaux d'isolation ou un changement d'émetteurs.

Ce service efface l'apprentissage SmartPI et force un retour en mode bootstrap.

Il efface aussi ce que les dépendances entre zones ont appris (les échanges de chaleur avec les voisins, l'ajustement d'une zone à deux sources de chaleur). Il garde les moyennes de l'entrée eau. La remise à zéro proposée par le formulaire de zone, par le formulaire Water and boiler, et par l'avis de réparation de l'entrée eau dans Home Assistant, est cette même remise à zéro ; voir [Dépendances entre zones, §2.2](zone_dependencies.md#22-remise-à-zéro-du-modèle-appris).

### `force_smartpi_calibration`

À utiliser si vous souhaitez lancer un cycle de calibration sans attendre le déclenchement automatique.

Ce service est utile si :

- les temps morts affichés semblent incohérents,
- la régulation fonctionne moins bien qu'avant,
- vous souhaitez rafraîchir l'apprentissage après un changement important des conditions réelles.

Si SmartPI est encore en phase bootstrap, la demande est ignorée.

### `reset_smartpi_integral`

À utiliser si l'intégrale a conservé une valeur inadaptée après un événement exceptionnel.

Exemples typiques :

- une longue coupure de chauffage,
- une fenêtre restée ouverte longtemps,
- toute situation où vous voulez garder le modèle appris mais repartir d'un état intégral neutre.
