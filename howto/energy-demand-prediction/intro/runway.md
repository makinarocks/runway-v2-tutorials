<!-- v2.2.0 에너지 수요 예측 MLOps 튜토리얼 신규 추가 | 2026-06-16 -->

# Runway에서 애플리케이션은?

Runway에서는 코드 편집기, 파이프라인 도구, 실험 추적 도구, 대시보드와 같이 필요한 도구를 **애플리케이션(앱)** 단위로 생성하여 사용합니다.

Runway는 Kubernetes 기반으로 동작하며 다양한 오픈소스를 Helm 차트로 배포할 수 있어, **원하는 도구를 조합하는 작업 환경을 자유롭게 구성**할 수 있습니다. 대신 각 앱에서 자체 UI와 CLI(Command Line Interface)를 가지고 있기 때문에 **실제 많은 작업이 Runway 콘솔이 아닌 각 앱의 화면**에서 이루어집니다.

---

## 애플리케이션 유형

앱은 배포 방식에 따라 세 가지로 구분됩니다.

| 유형 | 배포 방식 | 설명 | 이 튜토리얼에서 |
|------|----------|------|----------------|
| **플랫폼 앱** | 플랫폼 제공 | 별도 배포 없이 로그인하여 사용 | Gitea, MLflow |
| **카탈로그 앱** | 카탈로그에서 배포 | 사용자가 카탈로그 메뉴의 <p> 앱 목록(템플릿)에서 선택해 배포 | Code Server, Airflow |
| **커스텀 앱** | 직접 배포 | 사용자가 직접 구성하거나 <p> 공개된 헬름 차트로 배포 | 에너지 수요 예측 대시보드 |

이 세 가지 배포 방식을 모두 이 튜토리얼에서 직접 경험합니다.

---

<div class="pdf-pb"></div>

## 튜토리얼에서 사용하는 앱 목록

이 튜토리얼에서 배포하거나 접속하는 앱 전체 목록입니다.  
접속 URL의 `<your-...>` 플레이스홀더는 환경마다 다르며, `<your-runway-domain>`을 제외한 실제 값은 각 단계에 따라 생성됩니다.

| 앱 | 배포 방식 | 용도 | 접속 URL | 접속 가능 단계 |
|----|------|------|----------|---------------|
| **OpenBao** | 플랫폼 제공 | 시크릿 중앙 관리 | `https://openbao.<your-runway-domain>` | 프로젝트 생성 후 |
| **Gitea** | 플랫폼 제공 | DAG 코드 저장소 | `https://gitea.<your-runway-domain>` | 프로젝트 생성 후 |
| **Code Server** | 카탈로그에서 배포 | 코드 편집·실행 IDE | `https://<your-ide-hostname>.<your-runway-domain>` | 1단계 배포 완료 후 |
| **Apache Airflow** | 카탈로그에서 배포 | ML 학습 파이프라인 실행 및 스케줄링 | `https://<your-airflow-hostname>.<your-runway-domain>` | 3단계 배포 완료 후 |
| **MLflow** | 플랫폼 제공 | 실험 결과 조회 및 모델 레지스트리 | `https://mlflow.<your-runway-domain>` | 3단계 학습 완료 후 |
| **에너지 수요 예측 대시보드** | 커스텀 앱 | 추론 결과 조회 및 재학습 트리거 | `https://<your-gui-hostname>.<your-runway-domain>` | 5단계 배포 완료 후 |

사용 편의를 위해 아래 내용을 복사해 메모장에 붙여넣고, 배포 후 실제 URL을 채워두세요.

```
OpenBao: https://openbao.<your-runway-domain>
Gitea: https://gitea.<your-runway-domain>
Code Server: https://<your-ide-hostname>.<your-runway-domain>
Apache Airflow: https://<your-airflow-hostname>.<your-runway-domain>
MLflow: https://mlflow.<your-runway-domain>
에너지 수요 예측 대시보드: https://<your-gui-hostname>.<your-runway-domain>
```
