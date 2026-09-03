# Cluster local com Multipass (sem custo)

Alternativa 100% gratuita ao provisionamento via Terraform/AWS (`../terraform/`).
Mesma topologia — 1 control-plane + workers — mesmos comandos de `kubeadm`,
rodando em VMs leves na sua própria máquina em vez de EC2.

Por que isso existe: a infraestrutura em `../terraform/` continua sendo a
prova de domínio de IaC para AWS (parte do portfólio), mas o desenvolvimento
e os testes do dia a dia rodam aqui, para não gerar custo de nuvem contínuo.

## 1. Instalar o Multipass

No PowerShell (como Administrador):

```powershell
winget install Canonical.Multipass
```

Se não tiver `winget`, baixe o instalador em https://multipass.run/install

Reinicie o terminal depois de instalar.

## 2. Criar as VMs

```powershell
multipass launch --name k8s-control-plane --cpus 2 --memory 2G --disk 10G
multipass launch --name k8s-worker-1      --cpus 2 --memory 2G --disk 10G

# Opcional, se quiser um segundo worker (precisa de mais ~2GB de RAM livre)
# multipass launch --name k8s-worker-2 --cpus 2 --memory 2G --disk 10G
```

## 3. Conferir os IPs internos

```powershell
multipass list
```

Anote o IP de cada VM (formato `10.x.x.x`) — vai precisar deles no `kubeadm init`.

## 4. Entrar em cada VM e instalar containerd + kubeadm + kubelet + kubectl

```powershell
multipass shell k8s-control-plane
```

Dentro da VM (repita em cada worker também, trocando só o passo 5):

```bash
sudo apt-get update
sudo apt-get install -y containerd
sudo mkdir -p /etc/containerd
containerd config default | sudo tee /etc/containerd/config.toml
sudo systemctl restart containerd

sudo apt-get install -y apt-transport-https ca-certificates curl gpg
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.30/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.30/deb/ /' | sudo tee /etc/apt/sources.list.d/kubernetes.list
sudo apt-get update
sudo apt-get install -y kubelet kubeadm kubectl
sudo apt-mark hold kubelet kubeadm kubectl
```

## 5. Iniciar o cluster (somente na VM control-plane)

```bash
sudo kubeadm init --pod-network-cidr=192.168.0.0/16
```

Guarde o comando `kubeadm join ...` que aparece no final da saída — vai usar
nos workers.

Depois, ainda na control-plane:

```bash
mkdir -p $HOME/.kube
sudo cp /etc/kubernetes/admin.conf $HOME/.kube/config
sudo chown $(id -u):$(id -g) $HOME/.kube/config

kubectl apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.28.0/manifests/calico.yaml
```

## 6. Juntar os workers

Em cada VM worker, `multipass shell k8s-worker-1` e cole o comando
`kubeadm join` que você guardou no passo 5 (rode com `sudo`).

## 7. Verificar

De volta na control-plane:

```bash
kubectl get nodes
```

Todos devem aparecer `Ready` depois de ~1 minuto.

## Ligando e desligando (economia de RAM)

```powershell
# Libera a RAM de volta pro Windows quando não estiver usando
multipass stop k8s-control-plane k8s-worker-1

# Religa depois, com o estado preservado
multipass start k8s-control-plane k8s-worker-1
```

## Aplicando os manifests do projeto

Com `kubectl` configurado (passo 5), os manifests de `../k8s/` funcionam
exatamente como descrito no README daquela pasta — só que agora contra o
cluster local em vez da AWS. Único ajuste: as imagens Docker ainda precisam
estar publicadas no Docker Hub (o Multipass não enxerga as imagens locais
do seu Docker Desktop), então o passo de `docker build` + `docker push`
continua sendo necessário.

## Removendo tudo

```powershell
multipass delete k8s-control-plane k8s-worker-1
multipass purge
```
