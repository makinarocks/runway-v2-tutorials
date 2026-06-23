<!-- v2.2.0 에너지 수요 예측 MLOps 튜토리얼 신규 추가 | 2026-06-16 -->

# 부록 A. OpenBao Agent Injector 선행 조건

Pod에 시크릿이 자동 주입되려면 클러스터에 OpenBao Agent Injector 관련 선행 설정이 필요합니다. 이 부록은 해당 설정을 스크립트로 일괄 적용하는 절차를 안내합니다.

!!! info "Runway 2.2.1 미만인 버전에만 해당"
    Runway 2.2.1 이후 버전에서는 프로젝트 생성 시 아래 설정이 자동으로 이루어집니다. 이 부록의 절차가 필요하지 않습니다.

이 튜토리얼의 시크릿 자동 주입(Pod annotation → `/vault/secrets/creds.env`) 패턴이 동작하려면 클러스터에 다음 세 가지가 활성화되어 있어야 합니다.

| 조건 | 범위 | 적용 시점 |
|------|------|----------|
| OpenBao Agent Injector (mutating webhook + sidecar 자동 inject) | 클러스터 전체 | 클러스터당 1회 |
| OpenBao의 Kubernetes auth method 활성화 | OpenBao namespace 단위 | 프로젝트마다 1회 |
| 본인 프로젝트용 role (어느 SA가 어느 시크릿을 읽을 수 있는지 정의) | OpenBao namespace 단위 | 프로젝트마다 1회 |

Runway 2.2.0에서는 세 가지 모두 자동 설정이 되지 않습니다. 이 부록의 스크립트를 **클러스터 관리자 권한으로** 1회 실행해야 합니다.

---

## A-0. 필요 권한 {#permissions}

이 절차는 워크스페이스·프로젝트 관리자 권한으로는 부족합니다. 다음 권한이 모두 필요합니다.

- 클러스터 관리자 수준의 `kubectl` 인증 (Runway kubeconfig + Keycloak OIDC 로그인 — **부록 C**의 OIDC-Login 플러그인 필요)
- `runway-applications` namespace의 Helm release 수정 권한 (`openbao` release)
- OpenBao root 토큰 접근 권한 — `runway-applications/openbao-token` Secret의 `ROOT_TOKEN` 키

권한이 없으면 클러스터 관리자 권한 보유자에게 이 부록의 스크립트 실행을 요청하세요.

### 사전 설치

스크립트는 로컬 PC의 `kubectl`(+ OIDC-Login 플러그인)과 `helm`을 사용합니다. 설치가 안 된 경우 **[부록 C](c-local-cli.md)**를 먼저 완료하세요.

설치 후 현재 context가 올바른 클러스터를 가리키는지 확인합니다.

```bash
kubectl config current-context     # 본인 환경의 클러스터 이름
kubectl get ns runway-applications # 클러스터 관리자 권한일 때만 접근 가능
```

---

## A-1. 설정 스크립트 실행 {#setup}

`tutorials/energy-demand-prediction/scripts/setup-openbao-injector.sh`가 세 가지 설정을 한 번에 처리합니다.

=== "macOS / Linux"

    ```bash
    cd tutorials/energy-demand-prediction/scripts
    chmod +x ./setup-openbao-injector.sh
    ./setup-openbao-injector.sh <your-project-id>
    ```

=== "Windows (Git Bash / WSL)"

    ```bash
    cd tutorials/energy-demand-prediction/scripts
    bash setup-openbao-injector.sh <your-project-id>
    ```

!!! tip "이 스크립트는 멱등(idempotent)합니다"
    이미 적용된 단계는 자동으로 건너뜁니다. 같은 프로젝트에 여러 번 실행해도 안전합니다.

**기대 출력 (성공 시)**:

```
==> 사전 점검
  ✓ kubectl / helm / target namespace / OpenBao Pod 확인
==> OpenBao ROOT_TOKEN 확보
  ✓ ROOT_TOKEN 확보
==> Agent Injector (helm chart) 활성화
  ✓ ...
==> Kubernetes auth method 활성화
==> 정책 작성
==> role 생성 (bound_service_account_names='*', namespace=<ns>)
  ✓ role 생성 완료
==> 결과 확인
  ✓ 셋업 완료
```

!!! info "`bound_service_account_names='*'`의 의미"
    본인 namespace 안의 모든 ServiceAccount가 `secret/data/energy`를 읽을 수 있습니다. 신뢰 경계는 namespace + policy(`secret/data/energy` read only)입니다. 새 컴포넌트(카탈로그 앱, 커스텀 앱, KPO task Pod)가 새 SA를 만들어도 클러스터 관리자 개입 없이 자동으로 인증됩니다.

---

## A-2. 설정 검증 {#verify}

`verify-openbao-injector.sh`가 다음 4가지를 확인합니다. 클러스터 측 검증이므로 Pod이 떠있지 않아도 바로 실행 가능합니다.

1. Agent Injector deployment 존재 (클러스터 단위)
2. 본인 namespace의 Kubernetes auth method 활성화
3. role 존재 + namespace 바인딩 + policy 바인딩
4. policy의 read 경로가 `secret/data/energy` + `secret/metadata/energy`

=== "macOS / Linux"

    ```bash
    chmod +x ./verify-openbao-injector.sh
    ./verify-openbao-injector.sh <your-project-id>
    ```

=== "Windows (Git Bash / WSL)"

    ```bash
    bash verify-openbao-injector.sh <your-project-id>
    ```

성공 시 마지막에 `✓ 전체 검증 통과`가 출력됩니다.

!!! note "Pod 측 검증은 1단계에서"
    클러스터 측 설정이 완료됐으면, 실제 `/vault/secrets/creds.env` 마운트 확인은 본문 [1단계 1-3](../01-dev-env/03-verify.md#verify)의 Code Server 터미널에서 진행합니다.

---

## A-3. 롤백 {#rollback}

`rollback-openbao-injector.sh`가 설정을 되돌립니다. 실제 삭제 전 `yes` 입력 prompt로 안전 확인합니다.

=== "macOS / Linux"

    ```bash
    chmod +x ./rollback-openbao-injector.sh

    # 본인 namespace만 정리 (다른 프로젝트는 그대로 유지)
    ./rollback-openbao-injector.sh <your-project-id>

    # + 클러스터 전역 Agent Injector도 비활성화 (비상시에만)
    ./rollback-openbao-injector.sh <your-project-id> --cluster
    ```

=== "Windows (Git Bash / WSL)"

    ```bash
    bash rollback-openbao-injector.sh <your-project-id>            # namespace만
    bash rollback-openbao-injector.sh <your-project-id> --cluster  # + 클러스터 전역
    ```

!!! warning "OpenBao 시크릿은 건드리지 않습니다"
    이 스크립트는 auth method·role·policy만 제거합니다. 0단계에서 등록한 `secret/data/energy` 시크릿 자체는 삭제되지 않습니다. 완전히 정리하려면 OpenBao UI에서 `secret/energy`를 직접 삭제하세요.
