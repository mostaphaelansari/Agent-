# Plan d'implémentation

Roadmap historisée — ce qui a été fait, et ce qui peut suivre.

---

## ✅ Phase 0 — Refactor architectural (single agent + deterministic tool)

**Statut** : Fait le 2026-05-12.

- Diagnostic incident `browse` (sub-agent qui bouclait : 1.2M tokens, 95s).
- Suppression du sous-agent Strands en process.
- Création du tool déterministe `fetch_url(url)` (Python, pas de LLM).
- Renames : `agents/browser_agent.py` → `agents/web_tools.py`, `agents/chatbot_agent.py` → `agents/chatbot.py`.
- Patch IAM `BedrockAgentCoreBrowser` (manquait sur le rôle d'exécution).

**Résultat** : 95s → 24s, 1.2M tokens → 25K.

---

## ✅ Phase 1 — AgentCore Memory managée

**Statut** : Fait le 2026-05-12.

- Provisionnement d'une ressource Memory via `scripts/provision_memory.py` (`multi_agent_chatbot_memory-eQ2WgV4ihg`).
- Stratégies activées : `SUMMARY` + `USER_PREFERENCE`.
- Suppression de `memory.py` (SQLite).
- Intégration de `AgentCoreMemorySessionManager` dans `agents/chatbot.py`, fresh manager par invocation (pas de cache global).
- Stanza IAM `BedrockAgentCoreMemory` ajoutée.
- Env vars `AGENTCORE_MEMORY_ID` + `AGENTCORE_SUMMARY_STRATEGY_ID` + `AGENTCORE_PREF_STRATEGY_ID`.
- Activation `filter_restored_tool_context=True` (fix nécessaire — voir Phase 6 lessons).

**Tests validés** :
- STM (rappel intra-session immédiat) ✅
- LTM (rappel cross-session après ~7 min d'extraction async) ✅

---

## ✅ Phase 2 — Patch IAM `bedrock:CountTokens`

**Statut** : Fait dans la même PR que Phase 1.

- Ajout de `bedrock:CountTokens` à la stanza `BedrockModelInvocation`.
- Les triangles rouges cosmétiques dans X-Ray ont disparu.

---

## ✅ Phase 6 — Multi-agent via runtimes séparés (Option B)

**Statut** : Fait le 2026-05-12.

- Création de `browser_agent/agent_runtime.py` — sub-agent Strands borné, déployé comme **runtime AgentCore distinct**.
- Stanza IAM `InvokeBrowserAgent` (chatbot → browser_agent via HTTP).
- Tool `browse(goal)` côté chatbot : appelle `bedrock-agentcore:InvokeAgentRuntime` via boto3.
- `BROWSER_AGENT_ARN` ajouté en env var du chatbot.

**Bornes du browser_agent** :
- `SequentialToolExecutor`.
- Hook `ToolCallCap(max=6)` — cancelle au 7ème tool call.
- Prompt strict : 1 session, sélecteurs CSS spécifiques, JSON-only output.
- Retour parsé après strip des fences ```` ```json ```` éventuelles.

**Tests validés** :
- `fetch_url` path (Wikipedia Casablanca) → 29s ✅
- `browse` path forcé (example.com) → 26s ✅
- Tâche complexe (hôtel pas cher à Nice près de la plage) → 105s, 3 hôtels comparés avec prix réels ✅

**Lessons learned (debugging du `toolResult mismatch`)** :
- Le LLM peut émettre plusieurs toolUse blocks dans une seule réponse (parallèle implicite). `SequentialToolExecutor` ne change que l'exécution, pas l'émission.
- AgentCore Memory restaure l'historique avec les toolUse/toolResult blocks complets — sans `filter_restored_tool_context=True`, des incohérences apparaissent sur les conversations longues.
- Le CLI AWS a un `--cli-read-timeout` de 60s par défaut. Pour invoker un agent qui fait du browse, utiliser `--cli-read-timeout 600`.

---

## 🟡 Phase 4 — Production readiness (en cours / à compléter)

**Priorité : moyenne**. Avant tout trafic externe.

### Sous-tâches

| | Tâche | Effort |
|---|---|---|
| ☐ | **Tests pytest** : 4 scénarios canoniques (small talk / fetch_url / browse / memory recall) | 1 h |
| ☐ | **Streaming des réponses** : `BedrockModel(streaming=True)` côté chatbot + payload streamé côté AgentCore — perçu instantané sur les browse de 100s | 1-2 h |
| ☐ | **Sanitization du contenu web** : préfixer le retour de `fetch_url` avec un disclaimer pour réduire les risques de prompt injection depuis pages externes | 30 min |
| ☐ | **Trace correlation** : propager `traceparent` W3C dans `invoke_agent_runtime` pour lier les 2 runtimes dans une seule trace X-Ray | 1 h |
| ☐ | **CloudWatch alarmes** : taux d'erreur > 5%, latence p95 > 60s sur chatbot, > 120s sur browser_agent | 1 h |
| ☐ | **Métriques custom** : tokens par invocation, durée par tool, ratio fetch_url/browse | 30 min |
| ☐ | **Cap iterations chatbot** : ajouter un `ToolCallCap` hook côté chatbot aussi (max 3 — éviter de chaîner plus de tools que nécessaire) | 30 min |

---

## 🟢 Phase 3 — Tools additionnels (à la demande)

**Priorité : basse**. Quand un besoin concret apparaît.

Tous suivent le même pattern : **tool déterministe Python** si la séquence est connue, **runtime séparé** si décisions en chemin.

| Tool | Service AgentCore | IAM | Effort | Pattern |
|---|---|---|---|---|
| `run_python(code)` | CodeInterpreter | ✅ déjà OK | ~1 h | Tool déterministe |
| `search_web(query)` | tiers (Tavily/Brave) | Identity vault pour API key | ~2 h | Tool déterministe |
| `query_db(sql)` | DynamoDB/Athena | À ajouter | ~3 h | Tool déterministe |
| `send_email(to, body)` | SES | À ajouter | ~2 h | Tool déterministe |

Pour des tâches plus complexes (analyse de données multi-étapes, génération de rapports), envisager un 3ème runtime AgentCore dédié.

---

## 🟢 Phase 5 — Renames runtime (optionnel)

**Priorité : très basse**. Le nom `multi_agent_chatbot` est désormais correct (vraiment multi-agent depuis Phase 6). À garder.

---

## Ordre suggéré pour la suite

1. **Tests pytest** (Phase 4) — la moindre régression sera invisible sans ça maintenant qu'on a 2 runtimes à coordonner.
2. **Streaming** (Phase 4) — gros gain UX sur les browse longs.
3. **Sanitization + trace correlation** (Phase 4) — sécurité + lisibilité.
4. **CloudWatch alarmes** (Phase 4) — avant exposition trafic.
5. **`run_python` via CodeInterpreter** (Phase 3) — premier nouveau tool utile, IAM déjà en place.
