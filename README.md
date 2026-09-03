# SRE Self-Healing Platform

**Plataforma de confiabilidade com Chaos Engineering e auto-remediação** — do provisionamento de infraestrutura até a recuperação automática de falhas, sem intervenção humana.

![Status](https://img.shields.io/badge/status-em%20desenvolvimento-yellow)
![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.11-blue)
![Kubernetes](https://img.shields.io/badge/kubernetes-kubeadm-326ce5)

---

## O problema que este projeto resolve

A maioria dos sistemas de observabilidade para em "detectar e alertar" — e aí um humano precisa acordar, entender o problema e agir. Essa plataforma vai um passo além: fecha o ciclo completo de confiabilidade.

```
Falha injetada → Detecção via SLI/burn rate → Decisão automática → Remediação → Post-mortem gerado
```

O objetivo é medir, com dados reais gerados por falhas controladas, o quanto um sistema consegue se recuperar sozinho — e documentar isso como evidência de engenharia de confiabilidade, não só monitoramento.

---

## Arquitetura

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
        │                                   │  Controller        │
        │                                   └────────┬─────────┘
        │                                            │
┌───────▼───────┐                          ┌─────────▼─────────┐
│    ArgoCD      │                          │  Post-Mortem         │
│  (GitOps)      │                          │  Generator            │
└────────────────┘                          └──────────────────────┘
```

---

## Stack técnica

| Camada | Ferramenta |
|---|---|
| IaC | Terraform + AWS (VPC, EC2, IAM, S3) |
| Orquestração | Kubernetes (kubeadm) + Helm |
| Chaos Engineering | Chaos Mesh |
| Observabilidade | Prometheus, Grafana, Alertmanager |
| Auto-remediação | Controller Python custom |
| GitOps | ArgoCD |
| CI/CD | GitHub Actions |
| App | Python (FastAPI) com métricas Prometheus nativas |

---

## Roadmap

- [ ] **Fase 1 — Fundação**: microsserviços com dependência entre si, HPA, SLOs definidos
- [ ] **Fase 2 — Chaos Engineering**: experimentos de pod-kill, network delay, stress de CPU/memória, Game Days documentados
- [ ] **Fase 3 — Auto-remediação**: webhook Alertmanager → controller → ação corretiva automática
- [ ] **Fase 4 — Aprendizado contínuo**: post-mortem automático, dashboard histórico de reliability score

Detalhes de cada fase em [`docs/architecture.md`](docs/architecture.md).

---

## Estrutura do repositório

```
sre-self-healing-platform/
├── terraform/                 # IaC da infra AWS
├── k8s/                       # manifests + Helm charts
├── app/                       # microsserviços Python
├── chaos-experiments/         # definições Chaos Mesh (YAML)
├── remediation-controller/    # controller de auto-remediação
├── postmortem-generator/      # gerador de relatórios pós-incidente
├── docs/
│   ├── architecture.md
│   ├── game-days/             # um .md por experimento rodado
│   └── slo-definitions.md
└── .github/workflows/         # CI/CD
```

---

## Métricas que este projeto mede

- MTTD (tempo médio de detecção) — manual vs. automatizado
- MTTR (tempo médio de recuperação) — manual vs. automatizado
- % de incidentes simulados resolvidos sem intervenção humana
- Error budget consumido por experimento de caos

---

## Status

Projeto em desenvolvimento ativo. Acompanhe o progresso pelas issues e pelos commits.

## Licença

MIT — veja [LICENSE](LICENSE)