# Smart-PI: Documentation Technique et Scientifique

## 1. Introduction

**Smart-PI** est un régulateur proportionnel-intégral discret auto-adaptatif, disponible sous forme d'intégration autonome pour *Versatile Thermostat*. Il vise à remplacer un TPI à coefficients fixes par une boucle qui apprend en ligne le comportement thermique réel de la pièce.

Le code actuel repose sur trois idées directrices :

1. identifier un modèle thermique simple du premier ordre avec temps mort,
2. adapter la commande PI à partir de ce modèle,
3. geler ou limiter certaines adaptations quand le régime physique n'est pas jugé fiable.

Ce document décrit le comportement réellement implémenté dans le code SmartPI actuel.

Ce fork ajoute des extensions : les entrées connues (la chaleur échangée avec les zones voisines, et la
chaleur d'une deuxième source de chaleur dans la zone), la température de l'eau qui arrive aux émetteurs,
et l'appel de la chaudière (si la chaudière reçoit l'ordre de chauffer). Quand une extension change le
modèle, la chaîne de commande ou l'apprentissage, les sections ci-dessous en tiennent compte. Les
extensions elles-mêmes sont décrites dans [Dépendances entre zones et deuxième source de chaleur](zone_dependencies.md)
et [Entrée eau](water_input.md), et leurs tests dans [Tests](testing.md).

---

## 2. Modèle thermique et loi de commande

### 2.1 Modèle thermique utilisé

Le modèle thermique appris est :

$$ \frac{dT_{int}}{dt} = a \cdot u(t) - b \cdot (T_{int}(t) - T_{ext}(t)) $$

Avec :

- $T_{int}$ : température intérieure,
- $T_{ext}$ : température extérieure,
- $u(t) \in [0,1]$ : commande normalisée de l'actionneur actif,
- $a$ : gain thermique signé de l'actionneur, positif en HEAT et négatif en COOL,
- $b > 0$ : coefficient d'échange passif avec l'extérieur.

Un VTherm SmartPI est mono-mode : il apprend soit un modèle HEAT, soit un
modèle COOL. L'équation reste identique dans les deux cas ; le signe de $a$
porte le sens de l'action active.

La constante de temps associée est :

$$ \tau = \frac{1}{b} $$

Le modèle est volontairement simple. Le temps mort est appris séparément et injecté dans les heuristiques de réglage et dans les protections.

**Entrées connues.** Le modèle est étendu avec la chaleur échangée avec les zones voisines et la
chaleur apportée par une deuxième source :

$$ \frac{dT_{int}}{dt} = a \cdot u - b \cdot (T_{int} - T_{ext}) + \sum_i g_i (T_i - T_{int}) + \sum_j q_j $$

Ici, `g_i` (1/min) relie la zone à une température `T_i`, par exemple celle d'une zone voisine, et
`q_j` (°C/min) est une chaleur apportée directement, par exemple la chaleur de la deuxième source.

La loi de commande prend ces entrées en compte à travers deux valeurs qu'elle utilise déjà. Les deux
sont calculées à un seul endroit :

$$ b_{eff} = loss + \sum_i g_i, \quad T_{ref} = \frac{loss \cdot T_{ext} + \sum_i g_i T_i + \sum_j q_j}{b_{eff}} $$

Ici, `loss` est le `b` appris, ou les déperditions ajustées sans les voisins toujours connectés. Le décalage
de `T_ref` causé par le terme de chaleur est limité à `KNOWN_HEAT_MAX_SHIFT_C`. Sans entrées
connues, `b_eff = b` et `T_ref = T_ext`. L'apprentissage continue d'utiliser le `b` brut et `T_ext`
([Dépendances entre zones, §3](zone_dependencies.md#3-le-modèle-avec-entrées-connues)).

### 2.2 Chaîne de commande

La commande courante suit la structure :

$$ u = u_{FF} + u_{PI} $$

avec :

- `u_ff1` : feed-forward structurel dérivé de $b/a$,
- `u_ff2` : biais lent `FFTrim`,
- `u_ff3` : correction prédictive bornée de court horizon, activée par défaut et nulle hors contexte éligible,
- `u_pi` : terme PI discret.

La sortie réellement injectée dans le calcul est `u_ff_eff + u_pi`, puis elle passe par :

- limitation de vitesse,
- anti-windup,
- contraintes de cycle et protections ; avec un chauffage à eau, la sortie est limitée à
  `max_on_percent × max(φ, 0.05)`. C'est ce que donne une commande pleine avec le facteur d'eau `φ`
  (la chaleur que donne l'émetteur avec l'eau actuelle, comparée à celle donnée avec l'eau de référence).

Avec un chauffage à eau, le régulateur travaille en puissance, pour une température d'eau de
référence. La commande envoyée à l'émetteur est `power / max(φ, 0.05)`, au plus 1, avant la courbe
de vanne. L'apprentissage voit la commande multipliée par `φ`
([Entrée eau, §3.4](water_input.md#34-utilisation-dans-smartpi)).

---

## 3. Identification en ligne

### 3.1 Ordre de bootstrap

En phase `HYSTERESIS`, l'apprentissage suit un ordre strict :

1. le temps mort de la réponse active doit être fiable avant toute collecte
   utile pour `a` (`heat` en HEAT, `cool` en COOL),
2. le temps mort de la réponse passive doit être fiable avant toute collecte
   utile pour `b` (`cool` en HEAT, `heat` en COOL),
3. `b` doit progresser avant `a` via un soft gate.

Règles effectivement codées :

- `AB_MIN_SAMPLES_B = 8`,
- `AB_MIN_SAMPLES_A = 6`,
- `AB_MIN_SAMPLES_A_CONVERGED = 6`,
- `AB_A_SOFT_GATE_MIN_B = 8`.

SmartPI quitte `HYSTERESIS` lorsque `b` dispose de 8 mesures et `a` de
6 mesures. Les deux buffers continuent ensuite à se remplir jusqu'à
`AB_HISTORY_SIZE = 31` pendant la régulation SmartPI normale.

Dans une zone à deux sources de chaleur, la zone a un ajustement commun : un seul modèle ajusté pour
les deux sources ensemble. Dès que cet ajustement est publié, les valeurs `a` et `b` de SmartPI et ces
historiques sont fixés à partir de lui, puis SmartPI quitte `HYSTERESIS`. Hors de `HYSTERESIS`, les
valeurs `a` et `b` de l'ajustement, avec les compteurs qu'utilisent ses contrôles de fiabilité, sont
conservées à chaque calcul ([Dépendances entre zones, §5.5](zone_dependencies.md#55-apprentissage)).

La convergence de `b` utilisée pour l'apprentissage `a` avec détection de dérive repose sur `b_converged_for_a()` :

1. `learn_ok_count_b >= 11`,
2. `len(_b_hat_hist) >= 5`,
3. `MAD(b_hat)/Med(b_hat) <= 0.30`,
4. `range(last_5_b_hat)/Med(b_hat) <= 0.10`.

### 3.2 Fenêtre d'apprentissage réelle

Le code n'utilise plus de constante `WINDOW_MIN_MINUTES`. L'apprentissage repose aujourd'hui sur `LearningWindowManager` et sur une fenêtre glissante qui :

- exige une température extérieure disponible,
- respecte la gouvernance thermique sauf en calibration,
- bloque la collecte en bootstrap tant que les temps morts requis ne sont pas fiables,
- applique une pause après reprise (`LEARNING_PAUSE_RESUME_MIN = 20`),
- ancre le début de fenêtre après la fin du temps mort quand c'est nécessaire,
- surveille la stabilité de puissance via le coefficient de variation (`U_CV_MAX`),
- prolonge la fenêtre tant que la pente n'est pas jugée robuste,
- abandonne après `DT_MAX_MIN = 240` minutes si aucun signal robuste n'émerge,
- se met en pause tant qu'un verrou d'apprentissage est actif (porte d'un voisin ouverte ou en
  stabilisation, fenêtre ouverte chez un voisin, chaleur de la deuxième source pas encore connue,
  émetteur qui refroidit parce que la chaudière n'est pas appelée, ou qui se réchauffe une fois
  qu'elle démarre), et abandonne la
  fenêtre d'apprentissage en cours chaque fois qu'un verrou commence ou se termine,
- retire de la variation de température mesurée la part causée par les entrées connues : leur
  vitesse `known_rate = Σ g_i·(T_i − T) + Σ q_j`, intégrée sur la fenêtre,
- compte la puissance comme 0 tant que la zone demande de la chaleur et que la chaudière n'est pas
  appelée ([Entrée eau, §4](water_input.md#4-une-chaudière-qui-nest-pas-appelée)).

En pratique, la décision de soumettre ou d'étendre la fenêtre dépend de la robustesse de la pente, pas d'une durée minimale fixe.

### 3.3 Estimation robuste de pente

`ABEstimator.robust_dTdt_per_min()` applique des garde-fous sur la pente :

- amplitude minimale : `DT_DERIVATIVE_MIN_ABS = 0.05°C`,
- nombre minimal de changements de niveau : `OLS_MIN_JUMPS = 3`,
- significativité statistique : `OLS_T_MIN = 2.5`.

Les pentes validées alimentent ensuite `ABEstimator.learn()` qui :

- rejette les outliers physiques (`max_abs_dT_per_min = 0.35`),
- apprend `b` si `u < U_OFF_MAX`,
- apprend `a` si `u > U_ON_MIN`,
- exige `a > 0` en HEAT et `a < 0` en COOL,
- publie les valeurs avec une agrégation robuste (median ou weighted median selon la configuration).

### 3.4 Estimation de `a` et `b`

Le calcul suivi par `learn()` est :

- phase OFF :

$$ b = -\frac{dT/dt}{T_{int} - T_{ext}} $$

- phase ON :

$$ a = \frac{dT/dt + b \cdot (T_{int} - T_{ext})}{u} $$

Les historiques sont filtrés par median/MAD. La magnitude publiée de `a` est
bornée tout en conservant le signe du mode ; `b` reste strictement positif.

Avec un chauffage à eau, `u` est la puissance pour l'eau de référence (la commande multipliée par `φ`).

### 3.5 Estimation du temps mort

`DeadTimeEstimator` implémente une FSM indépendante. Les canaux décrivent la
réponse physique de la pièce, pas le sens de la transition de puissance :

- `deadtime_heat` : délai avant une montée de température,
- `deadtime_cool` : délai avant une baisse de température.

Ainsi, OFF -> ON apprend `heat` en mode HEAT mais `cool` en mode COOL.
La transition ON -> OFF apprend la réponse passive opposée.

La fiabilité devient vraie dès qu'au moins une mesure valide a été capturée sur le canal correspondant. Les valeurs publiées sont les moyennes des historiques `heat` et `cool`.

L'estimateur se met en pause tant qu'un verrou d'apprentissage est actif. Chaque fois qu'un verrou
commence ou se termine, l'observation en cours est remise à zéro. Avec un chauffage à eau, il reçoit
la commande, pas la puissance (0 pendant la production d'eau chaude sanitaire). Tant que la zone
demande de la chaleur et que la chaudière n'est pas appelée, il reçoit 0 : il ne chronomètre donc
aucune réponse. Le démarrage de la chaudière après un tel maintien n'est pas chronométré non plus :
ce retard vient de l'eau, pas de la vanne.

---

## 4. Contrôle Smart-PI

### 4.1 Gains PI

`GainScheduler.calculate()` applique la logique suivante :

1. si `tau`, le signe de `a` ou le temps mort actif ne sont pas fiables,
   repli sur `KP_SAFE = 0.55` et `KI_SAFE = 0.010`,
2. sinon calcul IMC/SIMC avec la magnitude du gain actif :

$$ K_p = \frac{1}{|a| \cdot (\lambda + L)} $$

3. calcul de $K_i = K_p / \tau$ ; avec des échanges de chaleur actifs, $\tau = 1 / (loss + \sum_i g_i)$, que les échanges raccourcissent (section 2.1),
4. bornage des gains puis application des gels de gouvernance.

Le temps mort actif est `deadtime_heat` en HEAT et `deadtime_cool` en COOL.

Important :

- l'amortissement near-band est appliqué continûment entre le bord de near-band configuré et la consigne,
- `NEAR_BAND_TAPER_KI_MIN` réduit le gain intégral plus fortement que `NEAR_BAND_TAPER_KP_MIN` près de la consigne.

### 4.2 Feed-forward

Le feed-forward structurel est calculé à partir de :

$$ k_{ff} = \frac{b_{eff}}{a} $$

et :

$$ u_{ff1} = clamp(k_{ff} \cdot (SP - T_{ref}), 0, 1) \cdot warmup\_scale $$

Sans entrées connues, `b_eff = b` et `T_ref = T_ext` (section 2.1). FF3, la trajectoire de
consigne, le jumeau thermique, la near-band et les fenêtres de la correction du feed-forward (FFTrim) utilisent les mêmes `b_eff`
et `T_ref`.

En COOL, $a < 0$ et donc $k_{ff} < 0$. Lorsque l'extérieur est plus chaud que
la consigne, les deux différences négatives produisent bien une puissance de
refroidissement positive.

Le `warmup_scale` n'est pas un simple interrupteur. Il dépend :

- du nombre d'apprentissages valides,
- du nombre de cycles depuis reset,
- de la fiabilité de `tau`.

À cela s'ajoutent :

- `u_ff2` : correction lente `FFTrim`,
- `ABConfidence` : politique de confiance sur `a/b`,
- repli en mode `AB_BAD` vers un feed-forward nul après `AB_BAD_PERSIST_CYCLES = 3`.

`FFTrim` est aussi gelé (`boiler_not_heating`) tant que la zone demande de la chaleur et que la
chaudière n'est pas appelée, et tant que l'émetteur de la zone se stabilise avant ou après une telle
période.

### 4.3 FF3

`ff3.py` ajoute une correction prédictive activée par défaut et désactivable par configuration :

- horizon calculé par `ff3_predictor.compute_ff3_horizon()` : `deadtime_cycles + FF3_RESPONSE_LOOKAHEAD_CYCLES`, borné entre `FF3_MIN_HORIZON_CYCLES` et `FF3_MAX_HORIZON_CYCLES`,
- prédiction ouverte locale avec le modèle discret exact 1R1C ZOH, sans appel à `ThermalTwin1R1C.step()` et sans mise à jour de l'observateur du jumeau,
- autorité max `FF3_MAX_AUTHORITY = 0.20`, multipliée par `authority_factor`,
- pas d'exploration `FF3_DELTA_U = 0.05`,
- scoring local quadratique avec coût de suivi, coût terminal, coût d'overshoot et coût de mouvement.

FF3 est désactivé si l'une des conditions suivantes n'est pas satisfaite :

- configuration activée,
- mode chauffage,
- température extérieure disponible,
- `tau` fiable,
- jumeau thermique initialisé et prédiction utilisable,
- pas de warm-up du jumeau,
- état stationnaire du jumeau valide,
- pas de calibration,
- pas de power shedding,
- pas de changement récent de consigne,
- pas en deadband,
- présence en near-band,
- régime de gouvernance compatible,
- pas de trajectoire active de source `setpoint`,
- présence d'un contexte crédible de perturbation externe.

Le contexte de perturbation accepte un modèle structurellement utilisable même si `model_reliable = False`.
Dans ce cas, `prediction_quality = "degraded"` et l'autorité est réduite via `FF3_UNRELIABLE_MODEL_AUTHORITY_FACTOR`.
`warming_up = True` et `T_steady_valid = False` restent des blocages absolus.

Le contexte de perturbation retenu ne repose pas sur `T_steady` comme faux négatif RMSE.
Il repose sur :

- un écart persistant entre la prédiction du jumeau et le comportement observé, interprété comme un résidu crédible (`bias_warning` ou `external_gain_detected` ou `external_loss_detected`),
- puis une cohérence dynamique via `perturbation_dTdt` et, si nécessaire, la pente thermique mesurée.

Dans ce contrat, FF3 n'est plus un optimiseur générique near-band. Il est réservé à la récupération de perturbation, et il ne provoque plus de restart de cycle dédié à l'entrée en deadband.

### 4.4 Gestion de consigne

`SmartPISetpointManager` pilote désormais une trajectoire analytique pour la branche P.
Cette trajectoire s’active uniquement lorsqu’un écart thermique significatif apparaît, que le modèle est jugé fiable et que la consigne évolue dans un sens nécessitant un lissage de la référence P.

Le principe est le suivant :

- la branche I continue d’utiliser la consigne brute,
- la branche P reçoit `filtered_setpoint`,
- la branche P conserve la consigne brute tant que la zone de freinage prédite n'est pas atteinte,
- un changement de consigne significatif arme une seule fois le freinage tardif ; en chauffage, son déclenchement compare ensuite la hausse prédite à la distance restante jusqu'à `target - LANDING_SAFETY_MARGIN_C`, sans réappliquer le seuil d'armement,
- un déclenchement dû à une perturbation continue d'exiger le seuil d'écart significatif,
- le modèle 1R1C exact appris, `deadtime_cool`, la latence restante du cycle et la puissance engagée sur le cycle servent à détecter cette zone de freinage,
- une trajectoire de freinage tardif douce est ensuite appliquée près de la cible tout en conservant une demande proportionnelle minimale positive,
- pour les trajectoires de consigne en chauffage, un cap d'atterrissage peut contraindre la commande interne après le calcul PI lorsque le modèle prédit que la chaleur stockée suffit à atteindre la cible,
- lorsque le freinage n'est plus nécessaire, la référence filtrée remonte progressivement vers la consigne brute avant l'arrêt de la trajectoire,
- pour une trajectoire issue d'un changement de consigne, l'entrée en phase `release` verrouille ensuite cette phase jusqu'à la fin de la trajectoire, sans retour vers `tracking`,
- `trajectory_active` indique si la trajectoire analytique est en cours,
- la trajectoire se termine seulement lorsque le handoff reste bumpless, que la température mesurée est assez proche de la cible et que l'état d'atterrissage autorise le relâchement, ou lorsque les conditions de fiabilité ne sont plus réunies.

Le cap d'atterrissage utilise la forme discrète du modèle 1R1C dans l'espace de commande interne linéaire :

$$
\alpha = e^{-b \cdot h}
$$

$$
T_{pred} = T_{ext} + (T - T_{ext}) \cdot \alpha + \frac{a}{b}(1-\alpha) \cdot u
$$

Le cap résout la commande maximale qui garde la température prédite sous `target - LANDING_SAFETY_MARGIN_C`. Il est appliqué après le calcul PI normal et avant les contraintes douces, de sorte que `u_pi` reste le diagnostic PI brut tandis que `landing_u_cap` explique la réduction finale de commande.

Le bloc canonique `live.setpoint` publie le résumé :

- `filtered_setpoint`,
- `trajectory_active`,
- `trajectory_source`,
- `landing_active`,
- `landing_reason`,
- `landing_u_cap`,
- `landing_coast_required`.

Le même payload dans les deux modes expose les détails de trajectoire et
d'atterrissage dans `live.analysis.trajectory` et `live.analysis.landing` :

- `start_setpoint`, `target_setpoint`, `tau_ref_min`, `elapsed_s` et `phase`,
- `pending_target_change_braking`, `braking_needed` et `model_ready`,
- `remaining_cycle_min`, `next_cycle_reference`, `bumpless_delta` et `bumpless_ready`,
- `setpoint_for_p_cap`, `predicted_temperature`, `predicted_rise` et `target_margin`,
- `release_allowed`, `time_to_target_min`, `release_blocked_by_slope`,
- `command_before_cap` et `command_after_cap`.

Le façonnage de référence reste limité à la branche P : la branche intégrale garde ainsi la consigne brute intacte, et l’apprentissage n’est pas perturbé. Le cap d'atterrissage est un gouverneur de commande post-PI séparé pour les trajectoires de consigne en chauffage ; il ne réécrit pas l'intégrale et ne change pas la courbe de linéarisation de vanne.

Le code actuel applique aussi une garde explicite sur la croissance positive de l'intégrale pendant les phases de rattrapage :

- après un changement de consigne significatif,
- après une reprise suivant `hvac_off`,
- après une reprise suivant détection d'ouverture,
- après une reprise suivant power shedding,
- pendant une trajectoire de récupération de perturbation.

Cette garde n'empêche pas la décharge de l'intégrale. Si l'intégrale mémorisée
s'oppose déjà au rattrapage demandé, la garde autorise
la résorption de cette mémoire jusqu'à zéro. Elle continue d'interdire toute
nouvelle accumulation au-delà de zéro dans la direction protégée. Une intégrale
négative héritée de l'ancienne consigne ne peut donc plus maintenir la pièce
sous une nouvelle consigne plus élevée.

La garde est relâchée seulement quand deux conditions sont réunies :

- l'erreur de release est revenue proche de l'échelle de la deadband configurée,
- la pente signée de rapprochement est devenue faible de manière persistante.

Le test de pente combine deux critères :

- un seuil relatif à la pente maximale observée pendant le rattrapage,
- un plancher absolu en `°C/h` pour éviter qu'un pic très faible ne rende la release trop permissive.

Le signal utilisé pour cette erreur de release dépend du contexte servo :

- pendant une trajectoire de consigne active, la release se base sur `error_p`, donc l'erreur de la consigne filtrée réellement suivie par la branche proportionnelle,
- hors trajectoire, la release se base sur `error_i`, donc l'erreur brute de consigne.

Cette séparation évite de garder la garde active alors que la trajectoire a déjà convergé vers sa référence filtrée et que le système est physiquement stabilisé sous la consigne brute.

L'erreur brute signée reste néanmoins la référence de sécurité : si `error_i <= 0`, la garde est relâchée immédiatement.

Cette pente est traitée symétriquement :

- en chauffage, une pente positive correspond à un rapprochement vers la consigne,
- en refroidissement, la pente est inversée logiquement pour conserver la même lecture physique.

Tant que la garde reste active, l'atténuation `TRAJECTORY_I_RUN_SCALE` peut limiter la croissance positive de l'intégrale pendant la trajectoire. Dès que la garde est relâchée, cette atténuation n'est plus appliquée : la branche I retrouve sa dynamique normale pour corriger le résidu statique.

Les reprises suivant détection d'ouverture et power shedding ajoutent une étape supplémentaire en chauffage :

- à la reprise, SmartPI arme d'abord un `I:HOLD` explicite,
- ce hold reste actif tant que la réaction de chauffe est encore dans la fenêtre exploitable de `deadtime_heat`,
- à la sortie de cette phase, SmartPI réévalue l'erreur résiduelle signée,
- il n'arme ensuite la garde positive que si cette erreur reste assez grande pour caractériser un vrai rattrapage,
- sinon la branche I revient directement à son fonctionnement normal.

Cette séquence évite de faire apprendre à l'intégrale une dynamique de reprise encore dominée par le temps mort de chauffe.

Les états transitoires de rattrapage ne sont pas restaurés après reboot :

- la trajectoire analytique active est purgée,
- la garde intégrale est remise à zéro,
- tout `integral_hold_mode` temporaire est effacé.

Le redémarrage repart donc d'un état PI persistant utile, mais sans restaurer des états servo transitoires qui ne sont plus physiquement valides hors de leur session d'origine. Le timestamp mural de la dernière régulation active valide est persisté et n'est pas rafraîchi lorsque le mode HVAC est OFF. Si l'inactivité dépasse 96 heures, la mémoire PI du contrôleur est purgée à la reprise tandis que le modèle A/B appris reste disponible.

### 4.5 Deadband, near-band et protections

`DeadbandManager` gère :

- une deadband symétrique avec hystérésis absolue (`DEADBAND_HYSTERESIS = 0.025`),
- une near-band asymétrique en chauffage,
- un calcul automatique de near-band si `deadtime_heat` et le modèle sont exploitables,
- un repli sur les seuils configurés sinon.

Les protections complémentaires actuellement présentes sont :

- `SmartPIGuards` : `guard_cut` et `guard_kick`,
- anti-windup par tracking,
- garde intégrale signée pendant les reprises, rattrapages et changements de consigne,
- `I:HOLD` explicite pendant la phase post-reprise liée à `deadtime_heat` pour `window_resume` et `power_shedding_resume` en chauffage,
- logique de maintien dans la deadband,
- avec un chauffage à eau, le maintien `no_heat` : la commande et l'intégrale sont maintenues pendant la production d'eau chaude sanitaire, et seule l'intégrale est maintenue tant que l'eau n'apporte presque pas de chaleur ([Entrée eau, §3.4](water_input.md#34-utilisation-dans-smartpi)),
- fenêtre ouverte chez un voisin, tant que la connexion avec ce voisin est active : la réaction choisie pour ce voisin. C'est soit l'arrêt, par le même chemin que l'arrêt sur fenêtre ouverte, avec sa reprise protégée, soit le preset hors-gel ou éco de la zone, et elle ne demande jamais plus de chauffage ou de refroidissement que la consigne ([Dépendances entre zones, §4.5](zone_dependencies.md#45-une-fenêtre-chez-un-voisin-connecté)).

Le calcul distingue aussi deux zones autour de la consigne :

- la deadband hystérétique, utilisée pour stabiliser l'état `in_deadband`,
- la deadband centrale réelle, définie par `abs(error_i) < deadband_c` une fois la constante de temps fiable, utilisée pour geler effectivement P et I.

Cette séparation évite de garder P et I gelés uniquement parce que l'hystérésis maintient encore l'état de deadband alors que l'erreur a déjà quitté la deadband configurée.

### 4.6 Auto-calibration

`AutoCalibTrigger` supervise l'algorithme hors loi de commande.

Comportement implémenté :

- snapshot initial quand `tau`, `deadtime_heat` et `deadtime_cool` sont fiables,
- fallback snapshot après `AUTOCALIB_DT_COOL_FALLBACK_DAYS = 7` jours sans `deadtime_cool` fiable,
- snapshot roulant tous les `AUTOCALIB_SNAPSHOT_PERIOD_H = 120` heures,
- vérification horaire,
- garde de cooldown `AUTOCALIB_COOLDOWN_H = 24` heures,
- seuils de stagnation `AUTOCALIB_A_MAD_THRESHOLD = 0.25` et `AUTOCALIB_B_MAD_THRESHOLD = 0.30`,
- sortie positive après au moins `AUTOCALIB_EXIT_NEW_OBS_MIN = 1` nouvelle observation sur `a` et `b` plus des temps morts cohérents,
- jusqu'à `AUTOCALIB_MAX_RETRIES = 3` essais,
- délai de retry planifié `AUTOCALIB_RETRY_DELAY_H = 6` heures.

Le cycle forcé est géré par `CalibrationManager` :

- `COOL_DOWN` jusqu'à `sp - 0.3°C`,
- `HEAT_UP`,
- `COOL_DOWN_FINAL` jusqu'au retour à `sp`.

Une calibration en cours est annulée quand la fenêtre ouverte d'un voisin déclenche une réaction.
Elle est aussi annulée quand la zone demande de la chaleur et que la chaudière n'est pas appelée,
car la réponse de la zone à la chaleur ne viendrait pas.

Les décisions du superviseur sont publiées sous forme d'événements Home Assistant. Chaque événement porte `entity_id`, l'entité climate du thermostat, et `name`, le nom du thermostat :

| Événement                          | Émis quand                                                                              | Autres champs                                                         |
| ---------------------------------- | --------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `smartpi_autocalib_snapshot_taken` | un snapshot est pris                                                                    | `reason` (`initial`, `fallback_7d`, `rolling`), `dt_cool_unavailable` |
| `smartpi_autocalib_triggered`      | une calibration est déclenchée par la stagnation ou par `force_smartpi_calibration`     | `triggered_params`, `retry_count`                                     |
| `smartpi_autocalib_success`        | une calibration supervisée se termine avec tous les critères remplis                    | `improved_params`, `retry_count`                                      |
| `smartpi_autocalib_retry`          | une calibration supervisée se termine avec des critères manquants et un retry est prévu | `retry_count`, `missing_criteria`                                     |
| `smartpi_autocalib_degraded`       | le dernier retry échoue et le modèle est marqué comme dégradé                           | `retry_count`                                                         |

Avant ce format, les événements portaient le nom du thermostat dans `entity_id`, et le Recorder de
Home Assistant le rejetait. Une automatisation qui cherchait le nom dans `entity_id` doit maintenant
le chercher dans `name`.

---

## 5. Architecture logicielle

### 5.1 Orchestrateurs

| Fichier                   | Classe           | Rôle                                                                     |
| ------------------------- | ---------------- | ------------------------------------------------------------------------ |
| `prop_algo_smartpi.py`    | `SmartPI`        | façade algorithmique, orchestration complète                             |
| `prop_handler_smartpi.py` | `SmartPIHandler` | pont Home Assistant : persistance, timer périodique, services, attributs |

### 5.2 Modules `smartpi/` réellement utilisés

| Module                 | Rôle principal                                           |
| ---------------------- | -------------------------------------------------------- |
| `const.py`             | constantes, enums, matrice de gouvernance                |
| `learning.py`          | estimation de pente, `ABEstimator`, `DeadTimeEstimator`  |
| `ab_aggregator.py`     | agrégation médiane ou médiane pondérée des mesures `a/b` |
| `ab_drift.py`          | détection et recentrage de dérive persistante sur `a/b`  |
| `learning_window.py`   | gestion des fenêtres d'apprentissage et de leurs gardes  |
| `gains.py`             | calcul et gel des gains                                  |
| `controller.py`        | PI discret, anti-windup, maintien, hystérésis            |
| `deadband.py`          | deadband et near-band                                    |
| `setpoint.py`          | trajectoire analytique de consigne et cap d'atterrissage en chauffage |
| `integral_guard.py`    | garde de croissance positive de l'intégrale pendant les phases de rattrapage |
| `feedforward.py`       | orchestration `u_ff1/u_ff2/u_ff3`                        |
| `ff_trim.py`           | biais lent causal et plans bornés de propriété trim/intégrale |
| `command_ownership.py` | projection de commande figée et suivi de rattachement du scheduler |
| `ff_trim_periodic.py`  | sélection de fenêtres d’équilibre périodique fermées sur la même phase |
| `ff_ab_confidence.py`  | politique de confiance sur `a/b`                         |
| `ff3.py`               | correction prédictive bornée FF3                         |
| `governance.py`        | décisions de gel                                         |
| `calibration.py`       | FSM de calibration forcée                                |
| `autocalib.py`         | supervision et déclenchement automatique                 |
| `guards.py`            | `guard_cut` et `guard_kick`                              |
| `thermal_twin_1r1c.py` | jumeau thermique et diagnostics prédictifs               |
| `diagnostics.py`       | payload canonique des diagnostics live                  |
| `diagnostic_history.py` | enveloppe versionnée et séries Recorder stables         |
| `tint_filter.py`       | filtrage adaptatif de la température intérieure          |
| `timestamp_utils.py`   | conversion monotonic / wall-clock                        |
| `effective_model.py`   | entrées connues et leur composition en `b_eff`, `T_ref`  |
| `dependencies.py`, `dependency_inputs.py` | éléments de dépendance, leur validation et leur état ; lectures d'un calcul |
| `dependency_model.py`  | entrées connues, verrous d'apprentissage, réaction à la fenêtre d'un voisin ; la zone à deux sources |
| `coupling_estimator.py` | apprentissage des coefficients d'échange `k`            |
| `zone_fit.py`, `source_model.py`, `source_candidates.py` | l'ajustement commun de la zone, la chaleur et les échelles de temps de la deuxième source |
| `split.py`, `source_settings.py`, `source_link.py` | règles de répartition et boucle d'allocation, réglages de la source, lien entre les sources |
| `water_input.py`       | le facteur d'eau `φ` à partir des données de Smart Boiler |
| `boiler_call.py`       | si la chaudière chauffe une zone qui l'appelle           |

Les modules des extensions sont décrits dans
[Dépendances entre zones, §6](zone_dependencies.md#6-composants-et-interfaces) et
[Entrée eau, §5](water_input.md#5-composants-et-interfaces).

### 5.3 Composition interne de `SmartPI`

Les composants instanciés par `SmartPI.__init__()` sont notamment :

```python
self.gov = SmartPIGovernance(name)
self.sp_mgr = SmartPISetpointManager(name, enabled=use_setpoint_filter)
self.ctl = SmartPIController(name)
self.est = ABEstimator(mode=aggregation_mode)
self.learn_win = LearningWindowManager(name)
self.deadband_mgr = DeadbandManager(name, near_band_deg)
self.calibration_mgr = CalibrationManager(name)
self.gain_scheduler = GainScheduler(name)
self.tint_filter = AdaptiveTintFilter(name, enabled=ENABLE_ADAPTIVE_TINT_FILTER)
self.integral_guard = SmartPIIntegralGuard(name)
self.twin = ThermalTwin1R1C(dt_s=SMARTPI_RECALC_INTERVAL_SEC, gamma=0.1)
self.guards = SmartPIGuards()
self.autocalib = AutoCalibTrigger(name)
self._ff_trim = FFTrim()
self._fftrim_observer = CausalFFTrimObserver(cycle_min)
self._fftrim_periodic_observer = PeriodicFFTrimObserver(cycle_min)
self._command_ownership = CommandOwnershipTracker()
self._ab_confidence = ABConfidence()
self.dependency_state = DependencyState()
self.dependency_model = DependencyModel(self.dependency_state)
self.water = WaterInput()
self.boiler = BoilerCall()
```

### 5.4 Persistance actuelle

L'observateur causal aligne une seconde timeline, pondérée dans le temps, de
la propriété des termes de commande avec celle de la puissance physique. Une
fenêtre stricte en deadband peut ainsi séparer le biais intégral stable
$J=mean(K_i integral)$ du déficit physique
$D=H-mean(u_{applied})$. Après trois fenêtres indépendantes cohérentes, une
transaction bornée applique
`delta_trim = alpha * lambda * (J + D)` et
`delta_u_i = -alpha * lambda * J`, donc
`delta_u_cmd = alpha * lambda * D`. Le même facteur `alpha` respecte l'autorité du
trim, les clamps FF, la capacité intégrale et la marge de commande. La
transaction s'exécute uniquement à la frontière de cycle après anti-windup,
dans le même espace de puissance signé en HEAT et COOL.
Les transferts inférieurs au seuil générique de précision de l'actionneur
restent ignorés, sauf lorsqu'ils réduisent strictement des contributions trim
et intégrale opposées. Ce compactage de propriété conserve les mêmes bornes
atomiques et le même invariant de commande.

Les observateurs stationnaire et périodique réutilisent une seule timeline de
puissance physique et de propriété, mais conservent des mesures thermiques, un
washout et des buffers de persistance séparés. La voie périodique accepte la
saturation basse et le maintien de l’intégrale comme parties d’un cycle créé
par la régulation. Elle ne ferme la fenêtre qu’après un retour à la même phase
thermique et au moins `max(6 cycles VT, 2 × temps mort)` ; elle n’a donc pas de
plancher fixe de trente minutes. Son estimation causale pondérée dans le temps
n’effectue jamais de transfert de propriété de l’intégrale ; les corrections
persistantes restent soumises au même taux d’apprentissage et à la même limite
d’autorité du trim.

Le payload actuel de `SmartPI.save_state()` contient notamment :

```python
{
    "est_state": {...},
    "dt_est_state": {...},
    "gov_state": {...},
    "ctl_state": {...},
    "sp_mgr_state": {...},
    "lw_state": {...},
    "db_state": {...},
    "cal_state": {...},
    "gs_state": {...},
    "integral_guard_state": {...},
    "twin_state": {...},
    "guards_state": {...},
    "ac_state": {...},
    "ff_v2_trim": {...},
    "tint_filter_state": {...},
    # seulement si utilisées :
    "dependencies": {...},          # échanges appris, ajustement de la zone et état de la source
    "dependency_reset_token": "...",  # la dernière remise à zéro demandée par un formulaire, appliquée une fois
    "water": {...},                 # disponibilité et moyennes de l'entrée eau
}
```

Les clés optionnelles sont décrites dans [Dépendances entre zones, §8](zone_dependencies.md#8-état-sauvegardé)
et [Entrée eau, §7](water_input.md#7-état-sauvegardé). Un payload sans ces clés se charge comme avant.

Seul le trim appris reste persistant. Les fenêtres de propriété, propositions
en attente et transferts d'intégrale sont transitoires ; après redémarrage,
l'observateur est reconstruit et le gel existant du trim pendant trois cycles s'applique toujours.

Pour les commandes switch, SmartPI prépare un snapshot de propriété figé juste
avant la soumission au scheduler. Le callback de début de cycle ne le rattache
que si le mode HVAC, les durées ON/OFF projetées et la puissance réalisée
correspondent. La quantification normale à la seconde est portée par la
projection ; une contrainte de durée minimale reste un segment causal rejeté.
Un callback absent ou divergent ne reconstruit jamais la propriété depuis
l’état courant du contrôleur. Les snapshots valve suivent la même règle de
propriété figée dans l’espace actionneur avant leur conversion vers l’espace
linéaire du modèle.

### 5.5 Diagnostics publiés

`diagnostics.py` expose les vues algorithmiques et live canoniques :

- `build_diagnostics()` : version compacte ou complète selon `debug_mode`,
- `build_published_diagnostics()` : payload `live` canonique du capteur de diagnostic dédié,
- `build_debug_diagnostics()` : accesseur de compatibilité qui renvoie le même payload canonique.

`diagnostic_history.py` enveloppe ce payload dans `schema_version`, `live` et le
bloc stable de huit séries `history`. Les entités normales excluent `live` du
Recorder ; la sous-classe debug l'enregistre directement. Le mode debug change
uniquement la conservation et n'ajoute pas de second bloc de diagnostic.

`analysis.learning.learn_ok_count` et `learn_skip_count` sont des compteurs
d'exécution relatifs au processus Home Assistant courant. Les compteurs
persistés du modèle restent internes et servent aux décisions de qualité du
modèle et de warm-up. Quand le jumeau thermique est exploitable, ses champs
publics sont exposés sous `analysis.twin`.

`live` porte aussi ces blocs, seulement quand ils sont utilisés :

- `dependencies` : le modèle composé (`b_eff`, `t_ref`), le verrou d'apprentissage, la réaction à la
  fenêtre, une entrée par voisin, et `second_source` pour la source principale d'une zone à deux
  sources de chaleur ; un thermostat qui sert de source additionnelle n'affiche que son rôle
  ([Dépendances entre zones, §9](zone_dependencies.md#9-diagnostics)) ;
- `water` : le facteur d'eau, sa source et les maintiens, pour un type de chauffage à eau ;
- `boiler` : si la chaudière est appelée, et le mode d'apprentissage qui en découle
  ([Entrée eau, §8](water_input.md#8-diagnostics)).

Avec des données d'eau, les valeurs `power` sont des puissances pour l'eau de référence, tandis que
`current_cycle_percent` et `next_cycle_percent` sont la commande envoyée à l'émetteur.

`feedforward.fftrim.command_ownership` expose le dernier état de rattachement,
son motif de rejet, la séquence de requête, les puissances et durées
demandées/projetées/réalisées, leur delta de puissance et l’indicateur de
contrainte temporelle. Ces valeurs sont des observations : elles expliquent
une acceptation ou un rejet causal sans modifier la décision d’apprentissage.

---

## 6. Constantes importantes

### 6.1 Apprentissage

| Constante                      | Valeur |
| ------------------------------ | ------ |
| `AB_HISTORY_SIZE`              | `31`   |
| `AB_MIN_SAMPLES_B`             | `8`    |
| `AB_MIN_SAMPLES_A`             | `6`    |
| `AB_MIN_SAMPLES_A_CONVERGED`   | `6`    |
| `AB_A_SOFT_GATE_MIN_B`         | `8`    |
| `AB_CONFIDENCE_MIN_SAMPLES_A`  | `11`   |
| `AB_CONFIDENCE_MIN_SAMPLES_B`  | `11`   |
| `AB_B_CONVERGENCE_MIN_SAMPLES` | `11`   |
| `AB_B_CONVERGENCE_MIN_BHIST`   | `5`    |
| `AB_B_CONVERGENCE_MAD_RATIO`   | `0.30` |
| `AB_B_CONVERGENCE_RANGE_RATIO` | `0.10` |
| `DT_DERIVATIVE_MIN_ABS`        | `0.05` |
| `OLS_MIN_JUMPS`                | `3`    |
| `OLS_T_MIN`                    | `2.5`  |
| `DT_MAX_MIN`                   | `240`  |
| `U_OFF_MAX`                    | `0.05` |
| `U_ON_MIN`                     | `0.20` |

### 6.2 Régulation

| Constante                     | Valeur  |
| ----------------------------- | ------- |
| `KP_SAFE`                     | `0.55`  |
| `KI_SAFE`                     | `0.010` |
| `MAX_STEP_PER_MINUTE`         | `0.25`  |
| `SETPOINT_BOOST_RATE`         | `0.50`  |
| `AW_TRACK_TAU_S`              | `120.0` |
| `SMARTPI_RECALC_INTERVAL_SEC` | `60`    |
| `LEARNING_PAUSE_RESUME_MIN`   | `20`    |

### 6.3 Bandes et consigne

| Constante                  | Valeur  |
| -------------------------- | ------- |
| `DEFAULT_DEADBAND_C`       | `0.05`  |
| `DEADBAND_HYSTERESIS`      | `0.025` |
| `DEFAULT_NEAR_BAND_DEG`    | `0.40`  |
| `NEAR_BAND_TAPER_KP_MIN`   | `0.75`  |
| `NEAR_BAND_TAPER_KI_MIN`   | `0.45`  |
| `SETPOINT_BOOST_THRESHOLD` | `0.3`   |
| `SETPOINT_BOOST_ERROR_MIN` | `0.3`   |

### 6.4 Auto-calibration et FF

| Constante                         | Valeur |
| --------------------------------- | ------ |
| `AUTOCALIB_SNAPSHOT_PERIOD_H`     | `120`  |
| `AUTOCALIB_DT_COOL_FALLBACK_DAYS` | `7`    |
| `AUTOCALIB_COOLDOWN_H`            | `24`   |
| `AUTOCALIB_A_MAD_THRESHOLD`       | `0.25` |
| `AUTOCALIB_B_MAD_THRESHOLD`       | `0.30` |
| `AUTOCALIB_MAX_RETRIES`           | `3`    |
| `AUTOCALIB_RETRY_DELAY_H`         | `6`    |
| `AUTOCALIB_EXIT_NEW_OBS_MIN`      | `1`    |
| `FF_TRIM_RHO`                     | `0.15` |
| `FF_TRIM_LAMBDA`                  | `0.05` |
| `AB_BAD_PERSIST_CYCLES`           | `3`    |
| `FF3_DELTA_U`                     | `0.05` |
| `FF3_MAX_AUTHORITY`               | `0.20` |
| `FF3_NEARBAND_GAIN`               | `0.50` |
| `FF3_MIN_HORIZON_CYCLES`          | `2`    |
| `FF3_RESPONSE_LOOKAHEAD_CYCLES`   | `2`    |
| `FF3_MAX_HORIZON_CYCLES`          | `8`    |
| `FF3_ACTION_SENSITIVITY_EPS_C`    | `1e-4` |
| `FF3_SCORE_EPS_COST`              | `1e-4` |
| `FF3_UNRELIABLE_MODEL_AUTHORITY_FACTOR` | `0.5` |

---

## 7. Gouvernance Safety-First

La matrice réellement utilisée dans `const.py` est :

| Régime           | Thermique (`a/b`) | Gains              |
| ---------------- | ----------------- | ------------------ |
| `WARMUP`         | `ADAPT_ON`        | `FREEZE`           |
| `EXCITED_STABLE` | `ADAPT_ON`        | `ADAPT_ON`         |
| `NEAR_BAND`      | `ADAPT_ON`        | `ADAPT_ON`         |
| `DEAD_BAND`      | `HARD_FREEZE`     | `HARD_FREEZE`      |
| `SATURATED`      | `ADAPT_ON`        | `FREEZE`           |
| `HOLD`           | `HARD_FREEZE`     | `SOFT_FREEZE_DOWN` |
| `PERTURBED`      | `HARD_FREEZE`     | `HARD_FREEZE`      |
| `DEGRADED`       | `HARD_FREEZE`     | `HARD_FREEZE`      |

Cette gouvernance est centrale :

- elle empêche d'apprendre sur des données jugées polluées,
- elle évite de faire bouger les gains dans des régimes peu informatifs,
- elle laisse malgré tout l'apprentissage thermique actif dans certains régimes contraints comme `SATURATED`.

---

## 8. Références

1. **Astrom K.J. et Hagglund T.**, travaux sur la régulation PID et l'usage de modèles FOPDT.
2. **Sundaresan K.R. et Krishnaswamy P.R.**, estimation des paramètres de délai et de constante de temps.
3. Régression **OLS** avec test t de Student pour la validation de pente.
