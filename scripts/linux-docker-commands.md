# Linux 发行版 Docker 安装命令清单

> agent-cluster doctor 在 Linux 上不自动执行安装（与 Windows/macOS 自动脚本不同），
> 仅展示本清单；按发行版选择对应命令后，再运行 `agent-cluster doctor --fix-docker` 验活。

## Debian / Ubuntu（apt）

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/$(. /etc/os-release && echo "$ID")/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$(. /etc/os-release && echo "$ID") $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
# 登出重登后生效；服务端启动：
sudo systemctl enable --now docker
```

## Fedora / RHEL 系（dnf）

```bash
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker
```

## Arch / Manjaro（pacman）

```bash
sudo pacman -Syu --noconfirm docker docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
# 登出重登后生效
```

## 验活

```bash
docker info
```

`docker info` 退出码 0 即就绪；随后可运行 `agent-cluster doctor` 确认全部预检通过。