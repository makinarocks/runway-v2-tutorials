<!-- v2.2.0 에너지 수요 예측 MLOps 튜토리얼 신규 추가 | 2026-06-16 -->

# 부록 A. 자가 빌드

본문은 사전 빌드된 이미지와 Helm 차트를 사용합니다. 코드를 수정하거나 본인 Gitea 레지스트리에 push하려는 경우에만 이 부록을 참고하세요.

| 자산 | 본문에서 사용하는 값 | 자가 빌드 위치 |
|------|-------------------|--------------|
| ML 이미지 | `gitea.try.mrxrunway.ai/tutorial-mrx/energy-ml:1.4.0` | **A-1** |
| GUI 이미지 | `gitea.try.mrxrunway.ai/tutorial-mrx/energy-gui:1.2.0` | **A-2** |
| GUI Helm 차트 | `energy-gui` `1.2.0` @ `https://gitea.try.mrxrunway.ai/api/packages/tutorial-mrx/helm` | **A-2** |

빌드 후 본인 Gitea 레지스트리에 push한 다음:

- **ML 이미지**: 1단계 OpenBao의 `ml_image` 값을 본인 이미지 경로로 업데이트합니다. DAG 파일 재 push는 불필요합니다.
- **GUI 이미지·차트**: 5단계 5-1의 Helm 리포지토리 URL과 차트를 본인 것으로 바꿉니다.

---

## 사전 조건 {#prerequisites}

- Docker 또는 Podman이 설치된 로컬 환경
- Gitea 개인 액세스 토큰 (`write:package` scope) — 0단계에서 발급한 개인 액세스 토큰 재사용 가능
- Helm CLI — 부록 B 참고

---

<!-- v2.4.0 RWP-1805 경로·태그 정정 — Dockerfile.ml 신설, 빌드 컨텍스트 명시, 태그 1.4.0 | 2026-09-02 -->
## A-1. ML 이미지 빌드 {#ml-image}

관련 파일 — 모두 `tutorials/energy-demand-prediction-v2/` 아래에 있습니다.

| 파일 | 위치 |
|------|------|
| `Dockerfile.ml` | 빌드 컨텍스트 루트 |
| `task_runner.py` · `config.py` · `requirements.txt` | `energy/` |

학습 데이터는 이미지에 넣지 않습니다. 공유 볼륨에서 읽습니다.

### 빌드 및 push

```bash title="ML 이미지 빌드 및 푸시 - 로컬 터미널"
cd tutorials/energy-demand-prediction-v2

GITEA_USER="<your-gitea-username>"
GITEA_PAT="<your-gitea-pat>"

echo "$GITEA_PAT" | docker login gitea.<your-runway-domain> -u "$GITEA_USER" --password-stdin

docker build -f Dockerfile.ml -t gitea.<your-runway-domain>/$GITEA_USER/energy-ml:1.4.0 .
docker push gitea.<your-runway-domain>/$GITEA_USER/energy-ml:1.4.0
```

!!! warning "옛 태그를 지우지 않기"
    태그는 **가이드 문서 버전에 맞춥니다.** 옛 가이드를 보는 사용자가 그 버전 이미지를 계속 쓰므로, 레지스트리에서 이전 태그를 지우면 그 사용자의 튜토리얼이 3단계에서 멈춥니다.

### OpenBao의 `ml_image` 값 업데이트

OpenBao UI → `secret/energy` → `ml_image` 키를 본인 이미지 경로로 변경합니다.

```
gitea.<your-runway-domain>/<your-gitea-username>/energy-ml:1.4.0
```

저장 후 다음 DAG run부터 새 이미지가 사용됩니다. DAG 파일을 재 push할 필요가 없습니다.

---

<!-- v2.4.0 경로 정정 — 실제 위치는 gui-assets/ 하위. 문서 경로대로는 빌드가 실패한다 | 2026-09-02 -->
<!-- v2.4.0 태그 1.1.5 → 1.2.0. nginx 설정을 이미지에 포함하도록 재구성 | 2026-09-03 -->
## A-2. GUI 이미지 + Helm 차트 빌드 {#gui}

관련 파일 — 모두 `tutorials/energy-demand-prediction-v2/gui-assets/` 아래에 있습니다.

| 파일 | 위치 |
|------|------|
| `Dockerfile.gui` | `gui-assets/` |
| GUI 소스 | `gui-assets/gui/` |
| nginx 설정 템플릿 | `gui-assets/gui/default.conf.template` |
| Helm 차트 | `gui-assets/helm/gui/` |

nginx 설정은 컨테이너가 뜰 때 렌더됩니다. OpenBao가 넣어 준 자격 증명을 `gui/16-runway-creds.envsh`가 환경 변수로 올리고, 템플릿의 `${RUNWAY_API_KEY}`·`${RUNWAY_BASE_DOMAIN}`이 치환됩니다. **자격 증명은 이미지에 들어가지 않습니다.**

### GUI 이미지 빌드 및 push

```bash title="GUI 이미지 빌드 및 푸시 - 로컬 터미널"
cd tutorials/energy-demand-prediction-v2/gui-assets

GITEA_USER="<your-gitea-username>"
GITEA_PAT="<your-gitea-pat>"

echo "$GITEA_PAT" | docker login gitea.<your-runway-domain> -u "$GITEA_USER" --password-stdin

docker build -f Dockerfile.gui -t gitea.<your-runway-domain>/$GITEA_USER/energy-gui:1.2.0 .
docker push gitea.<your-runway-domain>/$GITEA_USER/energy-gui:1.2.0
```

### Helm 차트 패키징 및 push

`helm/gui/values.yaml`의 `image.repository`를 본인 이미지로 먼저 수정합니다.

```yaml
image:
  repository: gitea.<your-runway-domain>/<your-gitea-username>/energy-gui
  tag: "1.2.0"
```

패키징 및 Gitea Helm 레지스트리에 업로드합니다.

```bash title="Helm 차트 패키징 및 업로드 - 로컬 터미널"
helm package helm/gui -d /tmp

curl -X POST \
  -u "$GITEA_USER:$GITEA_PAT" \
  -F "chart=@/tmp/energy-gui-1.2.0.tgz" \
  "https://gitea.<your-runway-domain>/api/packages/$GITEA_USER/helm/api/charts"
```

업로드 확인:

```bash title="Helm 차트 업로드 확인 - 로컬 터미널"
curl -u "$GITEA_USER:$GITEA_PAT" \
  "https://gitea.<your-runway-domain>/api/packages/$GITEA_USER/helm/index.yaml" \
  | head -15
```

### 5단계에 반영

5단계 5-1의 **헬름 리포지토리 URL**을 본인 리포지토리로 바꾸고, **차트 버전**에서 방금 올린 버전을 선택합니다.

```
https://gitea.<your-runway-domain>/api/packages/<your-gitea-username>/helm
```
