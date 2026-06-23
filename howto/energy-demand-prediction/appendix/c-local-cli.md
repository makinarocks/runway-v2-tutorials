<!-- v2.2.0 에너지 수요 예측 MLOps 튜토리얼 신규 추가 | 2026-06-16 -->

# 부록 C. 로컬 CLI 설정

**부록 A**의 OpenBao 설정 스크립트와 **부록 B**의 자가 빌드는 로컬 PC에서 클러스터에 접속하기 위한 CLI가 필요합니다.

!!! info "지원 OS"
    현재 이 문서는 **macOS** 기준으로 작성되어 있습니다. Linux 및 Windows 환경에 대한 안내는 추후 추가될 예정입니다.

| CLI | 용도 |
|-----|------|
| `kubectl` | Kubernetes 클러스터 조작 |
| OIDC-Login 플러그인 | Runway kubeconfig의 OIDC 인증 처리 — 없으면 `kubectl` 명령이 `unknown command "oidc-login"` 오류로 실패 |
| `helm` | Helm 차트 조작 (부록 A의 Agent Injector 활성화, 부록 B의 GUI 차트 빌드) |

---

## C-1. kubectl 설치 {#kubectl}

```bash title="kubectl 설치 - 로컬 터미널"
brew install kubectl
```

설치 확인:

```bash title="kubectl 설치 확인 - 로컬 터미널"
kubectl version --client
```

---

## C-2. OIDC-Login 플러그인 설치 {#oidc-login}

Runway의 kubeconfig는 OIDC 인증을 사용합니다. 이 플러그인이 없으면 `kubectl` 명령이 다음 오류로 실패합니다.

```
error: unknown command "oidc-login" for "kubectl"
```

```bash title="OIDC-Login 플러그인 설치 - 로컬 터미널"
brew install int128/kubelogin/kubelogin
```

설치 확인:

```bash title="OIDC-Login 설치 확인 - 로컬 터미널"
kubectl oidc-login --version
```

---

## C-3. Helm 설치 {#helm}

```bash title="Helm 설치 - 로컬 터미널"
brew install helm
```

설치 확인:

```bash title="Helm 설치 확인 - 로컬 터미널"
helm version
```

---

## C-4. Kubeconfig 받기 및 접속 확인 {#kubeconfig}

Runway 콘솔에서 kubeconfig 파일을 직접 다운로드할 수 있습니다.

> 오른쪽 상단 프로필 아이콘 > **계정 설정** > **다운로드 Kubeconfig** > **다운로드 Kubeconfig** 버튼 클릭

다운로드한 `config.yml` 파일을 `~/.kube/config` 경로에 저장합니다.

접속 확인 (첫 실행 시 브라우저가 열려 Keycloak SSO 로그인):

```bash title="클러스터 접속 확인 - 로컬 터미널"
kubectl config current-context   # 본인 환경의 클러스터 이름
kubectl get nodes                # 클러스터에 접근 가능한지 확인
```