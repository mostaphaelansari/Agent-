# Déploiement sur AWS Bedrock AgentCore

Ce guide décrit comment déployer `multi-agent-local` sur **AWS Bedrock AgentCore Runtime** à partir de l'entrypoint [`agent_runtime.py`](agent_runtime.py).

---

## 0. Pré-requis bloquants — à régler avant toute tentative de déploiement

> **Stop avant `agentcore launch`.** Le compte AWS `554025156005` accédé via le rôle SSO `AWSReservedSSO_PowerUserAccess_55241dcd1f635f6e` n'a actuellement **ni `iam:CreateRole` ni `iam:PassRole`**. Tous les chemins de déploiement échouent avec `AccessDeniedException` :
>
> | Chemin | Permission IAM requise |
> |---|---|
> | `agentcore launch` (CodeBuild) | `iam:PassRole` sur le rôle CodeBuild |
> | `agentcore launch --code-build` direct | `iam:PassRole` sur le rôle runtime |
> | Auto-création de rôle | `iam:CreateRole` |
>
> **Action préalable** : demander à un administrateur d'attacher au rôle SSO une policy `iam:PassRole` ciblant les rôles AgentCore SDK existants, avec la condition :
>
> ```json
> "Condition": {
>   "StringEquals": {
>     "iam:PassedToService": [
>       "bedrock-agentcore.amazonaws.com",
>       "codebuild.amazonaws.com"
>     ]
>   }
> }
> ```
>
> Sans cela, ne pas lancer le déploiement : il échouera tardivement et laissera des ressources orphelines facturées (Memory store, repo ECR, artefacts S3).

### Région

Les rôles AgentCore SDK pré-créés existent **uniquement en `eu-west-1`** :

- `AmazonBedrockAgentCoreSDKRuntime-eu-west-1-2292fc8165`
- `AmazonBedrockAgentCoreSDKCodeBuild-eu-west-1-2292fc8165`

Aucun équivalent en `us-west-2`. **Déployer en `eu-west-1`** sauf si l'admin crée aussi les rôles dans une autre région.

> Conséquence : `BEDROCK_MODEL_ID` doit passer de `us.anthropic.claude-sonnet-4-6` (profil cross-region US) à `eu.anthropic.claude-sonnet-4-6` (profil EU) au moment du déploiement.

---

## 1. Vue d'ensemble du déploiement

```
┌────────────────────────────────────────────────────────────┐
│  Local workstation                                         │
│  ─ agentcore configure  ─►  .bedrock_agentcore.yaml        │
│  ─ agentcore launch     ─►  CodeBuild ─► ECR image         │
│                                              │             │
└──────────────────────────────────────────────┼─────────────┘
                                               ▼
                          ┌───────────────────────────────────┐
                          │  AgentCore Runtime (eu-west-1)    │
                          │  ─ container `agent_runtime:app`  │
                          │  ─ exécution rôle SDK Runtime     │
                          │  ─ session_id géré par AgentCore  │
                          └──────────────┬────────────────────┘
                                         │ bedrock-runtime
                                         ▼
                                  ┌─────────────┐
                                  │  Bedrock    │
                                  │  Claude 4.6 │
                                  └─────────────┘
```

Le CLI `agentcore` (fourni par le paquet `bedrock-agentcore-starter-toolkit`) :

1. lit `agent_runtime.py`,
2. construit une image OCI via CodeBuild,
3. la pousse dans un repo ECR créé pour ce projet,
4. provisionne un Runtime AgentCore qui sert l'entrypoint `@app.entrypoint`.

---

## 2. Installation du toolkit local

Le projet n'a aujourd'hui que `bedrock-agentcore` (runtime SDK). Pour déployer, ajouter le starter toolkit :

```bash
pip install bedrock-agentcore-starter-toolkit
```

Vérifier :

```bash
agentcore --help
```

---

## 3. Préparer le code pour le déploiement

### 3.1 Fichiers à committer

L'image construite par CodeBuild prend tout ce qui est dans le répertoire courant. À garder :

- `agent_runtime.py` (entrypoint, **ne pas renommer**)
- `agents/` (les trois tiers)
- `memory.py`, `telemetry_setup.py`
- `requirements.txt`

À exclure via `.dockerignore` :

```gitignore
.venv/
__pycache__/
memory.db
screenshots/
.env
docker-compose.yml
```

### 3.2 Adapter `requirements.txt`

Ajouter le paquet runtime (déjà présent) et figer les versions si possible. Retirer `playwright` si vous ne packagez pas Chromium dans l'image — le Browser Agent utilise `AgentCoreBrowser` qui appelle le service AgentCore Browser et n'a **pas** besoin de Chromium local.

### 3.3 Mémoire — passer de SQLite à un store partagé

`memory.py` écrit dans `memory.db` sur le filesystem local. Le filesystem AgentCore est **éphémère par invocation** : l'historique conversationnel disparaîtra entre deux appels.

Deux options :

| Option | Effort | Recommandation |
|---|---|---|
| Utiliser AgentCore Memory (cloud) | moyen — réécrire `memory.py` autour de `bedrock-agentcore` Memory SDK | **Préféré** pour vrai multi-tour |
| Garder SQLite « best-effort » | aucun, mais l'historique se perd | Acceptable pour tester un déploiement initial |

Pour la première mise en production, garder SQLite en sachant que chaque session démarre à froid.

### 3.4 Variables d'environnement à passer au runtime

| Variable | Valeur pour `eu-west-1` |
|---|---|
| `AWS_REGION` | `eu-west-1` |
| `BEDROCK_MODEL_ID` | `eu.anthropic.claude-sonnet-4-6` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | retirer ou pointer vers un collecteur cloud (Jaeger local n'est plus accessible) |

---

## 4. Configurer et déployer

### 4.1 Configurer (une seule fois)

```bash
agentcore configure \
  --entrypoint agent_runtime.py \
  --name multi-agent-local \
  --region eu-west-1 \
  --execution-role arn:aws:iam::554025156005:role/AmazonBedrockAgentCoreSDKRuntime-eu-west-1-2292fc8165
```

Cela génère `.bedrock_agentcore.yaml` à la racine du projet. Vérifier que :

- `entrypoint: agent_runtime.py`
- `region: eu-west-1`
- l'ARN du rôle d'exécution est bien celui du SDK Runtime ci-dessus.

### 4.2 Lancer le déploiement

```bash
agentcore launch
```

Étapes effectuées par le toolkit :

1. Création (ou réutilisation) du repo ECR `bedrock-agentcore-multi-agent-local`.
2. Démarrage d'un job CodeBuild qui construit l'image depuis le `Dockerfile` généré.
3. Push de l'image dans ECR.
4. Création/mise à jour du Runtime AgentCore qui pointe sur cette image.

Suivre l'avancée du build :

```bash
agentcore status
```

---

## 5. Tester le runtime déployé

```bash
agentcore invoke \
  --payload '{"prompt": "Quelle est la météo à Paris ?"}' \
  --session-id test-session-1
```

Réponse attendue :

```json
{"result": "<réponse du chatbot>"}
```

Le `session_id` passé ici devient `context.session_id` côté `agent_runtime.py` ([agent_runtime.py:16](agent_runtime.py)).

---

## 6. Observabilité côté cloud

Jaeger local n'est pas joignable depuis le runtime. Deux remplacements :

- **CloudWatch Logs** : les `print` et logs Python remontent automatiquement dans le log group `/aws/bedrock-agentcore/runtimes/<runtime-id>`.
- **Traces OTEL** : exporter vers un collecteur ADOT déployé dans le compte (ou désactiver l'export en supprimant l'import de `telemetry_setup` dans `agent_runtime.py`).

Pour désactiver proprement l'export OTLP en cloud sans toucher au code, définir au runtime :

```bash
OTEL_SDK_DISABLED=true
```

---

## 7. Mise à jour

Toute modification de code + `agentcore launch` reconstruit et redéploie l'image. Le runtime garde le même ARN/endpoint.

---

## 8. Nettoyage

```bash
agentcore destroy
```

À supprimer **manuellement** ensuite (le toolkit ne les nettoie pas tous) :

- Repo ECR `bedrock-agentcore-multi-agent-local` (sinon facturation stockage)
- Memory store éventuellement créé par AgentCore (facturé à l'heure)
- Bucket S3 `bedrock-agentcore-codebuild-sources-*`
- Project CodeBuild `bedrock-agentcore-multi-agent-local-builder`

---

## 9. Récapitulatif — checklist avant `agentcore launch`

- [ ] L'admin a accordé `iam:PassRole` au rôle SSO sur les deux rôles SDK `eu-west-1`.
- [ ] `bedrock-agentcore-starter-toolkit` installé localement.
- [ ] `BEDROCK_MODEL_ID` mis à jour en profil EU.
- [ ] `.dockerignore` exclut `.venv`, `memory.db`, `screenshots/`, `.env`.
- [ ] Décision prise pour la mémoire (SQLite éphémère vs AgentCore Memory).
- [ ] `agentcore configure` exécuté, `.bedrock_agentcore.yaml` vérifié.
- [ ] Compte conscient des coûts résiduels (ECR, S3, Memory) à nettoyer si déploiement abandonné.
