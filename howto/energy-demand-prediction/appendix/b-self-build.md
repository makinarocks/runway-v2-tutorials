<!-- v2.2.0 에너지 수요 예측 MLOps 튜토리얼 신규 추가 | 2026-06-16 -->

# 부록 B. 자가 빌드

본문은 사전 빌드된 이미지와 Helm 차트를 사용합니다. 코드를 수정하거나 본인 Gitea 레지스트리에 push하려는 경우에만 이 부록을 참고하세요.

| 자산 | 본문에서 사용하는 값 | 자가 빌드 위치 |
|------|-------------------|--------------|
| ML 이미지 | `gitea.try.mrxrunway.ai/tutorial-mrx/energy-ml:1.3.2` | **B-1** |
| GUI 이미지 | `gitea.try.mrxrunway.ai/tutorial-mrx/energy-gui:1.1.5` | **B-2** |
| GUI Helm 차트 | `energy-gui` `1.1.5` @ `https://gitea.try.mrxrunway.ai/api/packages/tutorial-mrx/helm` | **B-2** |

빌드 후 본인 Gitea 레지스트리에 push한 다음:

- **ML 이미지**: 1단계 OpenBao의 `ml_image` 값을 본인 이미지 경로로 업데이트합니다. DAG 파일 재 push는 불필요합니다.
- **GUI 이미지·차트**: 6단계 2의 Helm 리포지토리 URL과 이미지 경로를 사용자 환경에 맞는 값으로 변경합니다.

---

## 사전 조건 {#prerequisites}

- Docker 또는 Podman이 설치된 로컬 환경
- Gitea 개인 액세스 토큰 (`write:package` scope) — 0단계에서 발급한 개인 액세스 토큰 재사용 가능
- Helm CLI — 부록 C 참고

---

## B-1. ML 이미지 빌드 {#ml-image}

관련 파일: `Dockerfile.ml`, `task_runner.py`, `config.py`, `requirements.txt`

### 빌드 및 push

```bash title="ML 이미지 빌드 및 푸시 - 로컬 터미널"
cd tutorials/energy-demand-prediction

GITEA_USER="<your-gitea-username>"
GITEA_PAT="<your-gitea-pat>"

echo "$GITEA_PAT" | docker login gitea.<your-runway-domain> -u "$GITEA_USER" --password-stdin

docker build -f Dockerfile.ml -t gitea.<your-runway-domain>/$GITEA_USER/energy-ml:1.3.2 .
docker push gitea.<your-runway-domain>/$GITEA_USER/energy-ml:1.3.2
```

### OpenBao의 `ml_image` 값 업데이트

OpenBao UI → `secret/energy` → `ml_image` 키를 본인 이미지 경로로 변경합니다.

```
gitea.<your-runway-domain>/<your-gitea-username>/energy-ml:1.3.2
```

저장 후 다음 DAG run부터 새 이미지가 사용됩니다. DAG 파일을 재 push할 필요가 없습니다.

---

## B-2. GUI 이미지 + Helm 차트 빌드 {#gui}

관련 파일: `Dockerfile.gui`, `gui/`, `helm-gui/`

### GUI 이미지 빌드 및 push

```bash title="GUI 이미지 빌드 및 푸시 - 로컬 터미널"
cd tutorials/energy-demand-prediction

GITEA_USER="<your-gitea-username>"
GITEA_PAT="<your-gitea-pat>"

echo "$GITEA_PAT" | docker login gitea.<your-runway-domain> -u "$GITEA_USER" --password-stdin

docker build -f Dockerfile.gui -t gitea.<your-runway-domain>/$GITEA_USER/energy-gui:1.1.5 .
docker push gitea.<your-runway-domain>/$GITEA_USER/energy-gui:1.1.5
```

### Helm 차트 패키징 및 push

`helm-gui/values.yaml`의 `image.repository`를 본인 이미지로 먼저 수정합니다.

```yaml
image:
  repository: gitea.<your-runway-domain>/<your-gitea-username>/energy-gui
  tag: "1.1.5"
```

패키징 및 Gitea Helm 레지스트리에 업로드합니다.

```bash title="Helm 차트 패키징 및 업로드 - 로컬 터미널"
helm package helm-gui -d /tmp

curl -X POST \
  -u "$GITEA_USER:$GITEA_PAT" \
  -F "chart=@/tmp/energy-gui-1.1.5.tgz" \
  "https://gitea.<your-runway-domain>/api/packages/$GITEA_USER/helm/api/charts"
```

업로드 확인:

```bash title="Helm 차트 업로드 확인 - 로컬 터미널"
curl -u "$GITEA_USER:$GITEA_PAT" \
  "https://gitea.<your-runway-domain>/api/packages/$GITEA_USER/helm/index.yaml" \
  | head -15
```

### 5단계에 반영

5단계 5-2의 **Helm 리포지토리 URL**을 사용자 환경에 맞는 값으로 변경합니다.

```
https://gitea.<your-runway-domain>/api/packages/<your-gitea-username>/helm
```
