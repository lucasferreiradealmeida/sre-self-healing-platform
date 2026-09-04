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

## 4. Entrar em cada VM e preparar o nó (containerd + kubeadm + kubelet + kubectl)

```powershell
multipass shell k8s-control-plane
```

Dentro da VM (repita **igual** em cada worker; só o passo 5 é exclusivo da
control-plane):

```bash
# --- pré-requisitos do kubeadm: sem isso o 'kubeadm init' falha ou o CNI não sobe ---

# swap desligado (requisito do kubelet)
sudo swapoff -a
sudo sed -i '/\bswap\b/ s/^\([^#]\)/#\1/' /etc/fstab

# módulos de kernel
cat <<'EOF' | sudo tee /etc/modules-load.d/k8s.conf
overlay
br_netfilter
EOF
sudo modprobe overlay
sudo modprobe br_netfilter

# sysctl de rede (roteamento de pods e bridge visível ao iptables)
cat <<'EOF' | sudo tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
sudo sysctl --system

# --- containerd ---
sudo apt-get update
sudo apt-get install -y containerd apt-transport-https ca-certificates curl gpg
sudo mkdir -p /etc/containerd
containerd config default | sudo tee /etc/containerd/config.toml >/dev/null
# driver de cgroup = systemd (tem que bater com o default do kubelet)
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
sudo systemctl restart containerd
sudo systemctl enable containerd

# --- repositório do Kubernetes + binários ---
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.30/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.30/deb/ /' | sudo tee /etc/apt/sources.list.d/kubernetes.list
sudo apt-get update
sudo apt-get install -y kubelet kubeadm kubectl
sudo apt-mark hold kubelet kubeadm kubectl
sudo systemctl enable kubelet
```

## 5. Iniciar o cluster (somente na VM control-plane)

```bash
sudo kubeadm init \
  --pod-network-cidr=192.168.0.0/16 \
  --apiserver-advertise-address=<IP_DA_CONTROL_PLANE>
```

O `--apiserver-advertise-address` fixa o IP interno da VM (o `10.x`/`172.x` do
passo 3). Sem ele o kubeadm pode anunciar a interface errada e os workers não
conseguem se juntar.

Guarde o comando `kubeadm join ...` que aparece no final da saída — vai usar
nos workers. Se perder, gere de novo na control-plane:

```bash
kubeadm token create --print-join-command
```

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
kubectl get pods -A
```

Todos os nós devem aparecer `Ready` e todos os pods de `kube-system`
(`calico-node`, `calico-kube-controllers`, `coredns`, `kube-proxy`, etc.)
`Running` depois de ~2 minutos.

Nos primeiros ~60–90 s é normal ver:

- nós em `NotReady` até o `calico-node` de cada um passar de `Init:x/3` para `Running`;
- erros `i/o timeout` no log do CoreDNS enquanto o Calico ainda está programando
  as rotas entre os nós — some sozinho.

Teste rápido de DNS/rede entre nós (deve responder `10.96.0.1`):

```bash
kubectl run dnstest --image=busybox:1.36 --restart=Never --rm -i -- \
  nslookup kubernetes.default.svc.cluster.local
```

## 8. Acessar o cluster pelo Windows (sem `multipass shell`)

Com `kubectl` instalado no host (`winget install Kubernetes.kubectl`):

```powershell
mkdir $HOME\.kube -Force
multipass exec k8s-control-plane -- sudo cat /etc/kubernetes/admin.conf > $HOME\.kube\config
kubectl get nodes
```

O `admin.conf` já aponta para `https://<IP_DA_CONTROL_PLANE>:6443`, que é
roteável a partir do host. Se já tiver outros contextos no `~/.kube/config`,
salve num arquivo separado e use `$env:KUBECONFIG` em vez de sobrescrever.

## 9. Fixar os IPs das VMs — **faça isso antes de qualquer `stop`/`start`**

O Multipass no Windows usa o "Default Switch" (NAT com DHCP do Windows). Num
`multipass stop`/`start` — ou ao mudar RAM/CPU, que exige a VM parada — a VM
**pode receber um IP novo**. Se o IP da **control-plane** mudar, o cluster
quebra: os certificados do kubeadm e os manifests estáticos estão amarrados ao
IP do `kubeadm init`, e etcd + apiserver entram em `CrashLoopBackOff` com
`no route to host`.

Prevenção: fixe o IP atual de cada VM como endereço estático adicional em
`eth0`, mantendo o DHCP. Rode **dentro de cada VM** (troque o IP e o MAC pelos
valores reais de `ip -4 addr show eth0` e `cat /sys/class/net/eth0/address`):

```bash
sudo tee /etc/netplan/99-extra-static.yaml >/dev/null <<EOF
network:
  version: 2
  ethernets:
    default:
      match:
        macaddress: "<MAC_DA_ETH0>"
      dhcp4: true
      addresses:
        - <IP_ATUAL_DA_VM>/20
EOF
sudo chmod 600 /etc/netplan/99-extra-static.yaml
sudo netplan apply
```

O gateway/máscara do Default Switch (`/20`, gw `.1` da subnet) é estável
enquanto o host não reiniciar.

### Se a control-plane já quebrou por troca de IP

1. Descubra o IP que os certs esperam:
   `multipass exec k8s-control-plane -- sudo grep advertise-address /etc/kubernetes/manifests/kube-apiserver.yaml`
2. Adicione esse IP de volta ao `eth0` com o bloco netplan acima (usando o IP
   **antigo**), `sudo netplan apply`.
3. `sudo systemctl restart kubelet` e aguarde etcd/apiserver saírem do
   CrashLoopBackOff (`sudo crictl ps`).

> Se o **host** reiniciar e a subnet inteira mudar, refaça o passo 9 com os
> IPs novos em todas as VMs (e `kubectl` do host aponta pro IP no
> `~/.kube/config`).

## Observabilidade

Depois que os SLIs estiverem sendo expostos pelos serviços, instale o
kube-prometheus-stack seguindo [`../k8s/monitoring/README.md`](../k8s/monitoring/README.md)
— inclui os values enxutos para caber nas VMs e a limitação conhecida de
latência da API (fsync lento do disco virtual).

## Versões de referência (deste setup)

| Componente | Versão |
|---|---|
| Imagem Multipass | Ubuntu 26.04 LTS |
| kubeadm / kubelet / kubectl | v1.30.x |
| containerd | 2.x (do apt do Ubuntu) |
| CNI | Calico v3.28.0 |
| Helm | v3.16.x |
| kube-prometheus-stack | chart 89.x |

## Ligando e desligando (economia de RAM)

> ⚠️ Só é seguro depois de fixar os IPs (passo 9). Sem isso, um `start` pode
> trocar o IP da control-plane e derrubar o cluster.

```powershell
# Libera a RAM de volta pro Windows quando não estiver usando
multipass stop k8s-control-plane k8s-worker-1

# Religa depois, com o estado preservado
multipass start k8s-control-plane k8s-worker-1
```

Ao religar, a API da control-plane pode levar 1–2 min para responder e os
pods de `sre-platform`/`monitoring` reiniciam (ficam todos no worker). Confira
com `kubectl get pods -A` até estabilizar.

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
