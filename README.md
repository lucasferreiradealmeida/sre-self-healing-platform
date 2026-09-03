# SRE Self-Healing Platform — Chaos Engineering & Auto-Remediation

> Projeto de portfólio para elevar posicionamento de SRE Júnior → Pleno/Sênior
> Autor: Lucas Ferreira de Almeida

---

## 1. Objetivo do projeto

Construir uma plataforma que demonstre o **ciclo completo de confiabilidade**: detectar degradação, decidir automaticamente a ação corretiva, executar remediação sem intervenção humana, e aprender com o incidente através de post-mortems automatizados.

O diferencial não é "ter Prometheus e Grafana rodando" — isso é básico. O diferencial é provar, com dados reais gerados por falhas injetadas propositalmente, que o sistema se recupera sozinho e que você sabe medir, quantificar e comunicar esse comportamento.

**Por que isso muda o jogo:** Chaos Engineering e auto-remediação são práticas raras em portfólios de profissionais júnior/pleno no Brasil. É o tipo de projeto que faz um recrutador técnico ou engenheiro sênior mudar a régua de avaliação ao ler seu currículo ou GitHub.

---

## 2. Arquitetura geral

```
┌─────────────────────────────────────────────────────────────┐
│                        AWS (Terraform)                       │
│  VPC multi-AZ · EC2 · IAM · S3 (state + relatórios)          │
└───────────────────────────┬───────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │   Kubernetes     │
                    │   (kubeadm)      │
                    └────────┬────────┘
        ┌────────────────────┼────────────────────┐
        │                    │                     │
┌───────▼───────┐  ┌─────────▼────────┐  ┌─────────▼────────┐
│ Serviços App   │  │  Chaos Mesh      │  │ Prometheus +      │
│ (API Python,   │  │  (injeção de     │  │ Grafana +          │
│ multi-réplica, │  │  falhas)         │  │ Alertmanager       │
│ com deps)      │  │                  │  │                    │
└───────┬───────┘  └──────────────────┘  └─────────┬──────────┘
        │                                            │
        │                                   ┌────────▼─────────┐
        │                                   │  Webhook          │
        │◄──────────── ação corretiva ──────┤  Auto-Remediation │
        │                                   │  Controller       │
        │                                   │  (Python)         │
        │                                   └────────┬─────────┘
        │                                            │
┌───────▼───────┐                          ┌─────────▼─────────┐
│    ArgoCD      │                          │  Gerador de         │
│  (GitOps /     │                          │  Post-Mortem         │
│   rollback)    │                          │  automático (MD)     │
└────────────────┘                          └──────────────────────┘
```

---

## 3. Componentes técnicos

| Camada | Ferramenta | Já domina? | Papel no projeto |
|---|---|---|---|
| IaC | Terraform + AWS | ✅ | Provisiona VPC multi-AZ, EC2, IAM, S3 |
| Orquestração | Kubernetes + Helm | ✅ | Roda os serviços e a stack de observabilidade |
| App alvo | API Python (Flask/FastAPI) + métricas Prometheus nativas | ✅ | Sistema com dependências entre serviços (não isolado) |
| Chaos Engineering | **Chaos Mesh** (CNCF) | 🆕 | Injeta pod-kill, network delay, CPU/memory stress |
| Observabilidade | Prometheus + Grafana + Alertmanager | ✅ | Detecta degradação via SLIs/burn rate |
| Auto-remediação | **Controller custom em Python** (webhook Alertmanager → k8s API) | 🆕 | Coração do projeto: decide e executa ação corretiva |
| GitOps | **ArgoCD** | 🆕 | Rollback automático, versionamento de estado desejado |
| Post-mortem | Script Python (puxa métricas + eventos do incidente) | 🆕 | Gera relatório automático em Markdown |
| CI/CD | GitHub Actions | ✅ | Quality gate, build, deploy |

---

## 4. Roadmap por fases

### Fase 1 — Fundação (evoluir o que já existe)
- [ ] Expandir a API atual para 2-3 microsserviços com dependência entre si (ex: serviço A chama B, que chama C) — simula sistema real, não isolado
- [ ] Configurar HPA (Horizontal Pod Autoscaler) baseado em métricas customizadas
- [ ] Redefinir SLOs por serviço (disponibilidade, latência p99, taxa de erro)
- [ ] Garantir dashboards Grafana por serviço + visão agregada

### Fase 2 — Chaos Engineering
- [ ] Instalar Chaos Mesh no cluster
- [ ] Criar experimentos: `PodChaos` (kill), `NetworkChaos` (latência/perda de pacote), `StressChaos` (CPU/memória)
- [ ] Rodar "Game Days" documentados: hipótese → execução → medição de impacto real no SLO
- [ ] Documentar cada experimento com metodologia (formato: hipótese, blast radius, resultado, aprendizado)

### Fase 3 — Auto-remediação (o diferencial)
- [ ] Configurar Alertmanager para disparar webhook em burn rate crítico
- [ ] Construir controller Python que recebe o webhook e decide a ação:
  - Escalar réplicas
  - Isolar serviço com problema (circuit breaker via rede/label)
  - Rollback automático via ArgoCD
- [ ] Testar o loop completo: caos injetado → detecção → remediação → recuperação medida

### Fase 4 — Aprendizado contínuo
- [ ] Script que gera post-mortem automático em Markdown a cada incidente (timeline, métricas, causa raiz sugerida, MTTD/MTTR reais)
- [ ] Dashboard histórico de "reliability score" / burn rate ao longo do tempo
- [ ] README do repositório com storytelling completo do projeto (problema → solução → resultado)

*Sem prazo fixo — você indicou ritmo livre. Sugestão: trate cada fase como um marco, feche uma antes de abrir a próxima, e documente no GitHub conforme avança (commits frequentes valem mais que um push final gigante).*

---

## 5. Métricas de sucesso do próprio projeto

Para poder falar em entrevista com números reais:

- MTTD (tempo médio de detecção) antes vs. depois da automação
- MTTR (tempo médio de recuperação) com remediação manual vs. automática
- % de incidentes simulados resolvidos sem intervenção humana
- Error budget consumido por experimento de caos
- Redução de "toil" (tempo economizado por não precisar agir manualmente)

---

## 6. Estrutura sugerida do repositório

```
sre-self-healing-platform/
├── README.md                  # storytelling do projeto
├── terraform/                 # IaC da infra AWS
├── k8s/                       # manifests + Helm charts
├── app/                       # microsserviços Python
├── chaos-experiments/         # definições Chaos Mesh (YAML)
├── remediation-controller/    # código do controller de auto-remediação
├── postmortem-generator/      # script de geração de relatórios
├── docs/
│   ├── architecture.md
│   ├── game-days/             # um .md por experimento rodado
│   └── slo-definitions.md
└── .github/workflows/         # CI/CD
```

---

## 7. Como isso aparece no currículo depois de pronto

Frases que esse projeto te habilita a escrever (com números reais do próprio projeto):

> "Projetei e implementei plataforma self-healing com Chaos Engineering (Chaos Mesh) e auto-remediação, reduzindo MTTR simulado em X% através de detecção via burn rate e remediação automática via controller customizado."

> "Conduzi Game Days estruturados para validar SLOs sob falha controlada, documentando blast radius e aprendizados em formato de post-mortem."

---

## 8. Próximo passo imediato

Começar pela Fase 1: expandir a API atual para simular dependência entre serviços — é a base que sustenta todo o resto do projeto (sem múltiplos serviços interdependentes, o chaos engineering não tem muito o que "quebrar" de forma interessante).
