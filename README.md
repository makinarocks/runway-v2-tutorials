# Runway v2 Tutorials

Makinarocks Runway v2 AI 플랫폼에서 ML 워크플로우를 실행하는 방법을 보여주는 튜토리얼 모음입니다.

## 디렉토리 구조

```
runway-v2-tutorial/
├── README.md                  # 전체 튜토리얼 인덱스 (이 파일)
├── CLAUDE.md                  # Claude Code용 프로젝트 가이드
├── .gitignore
└── tutorials/
    ├── _template/             # 새 튜토리얼 스캐폴드용 템플릿
    │   ├── README.md
    │   └── requirements.txt
    └── <tutorial-name>/       # 각 튜토리얼은 독립된 디렉토리
        ├── README.md          # 튜토리얼 설명 및 실행 방법 (필수)
        ├── requirements.txt   # Python 의존성 (필수)
        ├── dataset/           # 샘플 데이터 (선택)
        └── ...                # 파이프라인/스크립트 코드
```

## 튜토리얼 목록

플랫폼 변경이 잦은 시기라, 각 튜토리얼이 **어느 Runway 버전을 기준으로 작성됐는지** 함께 적습니다.
대상 버전과 다른 플랫폼에서는 절차나 코드가 맞지 않을 수 있습니다.

| 이름 | 주제 | 대상 플랫폼 | 가이드 문서 |
|------|------|------------|------------|
| [energy-demand-prediction-v2](tutorials/energy-demand-prediction-v2/) | 열수요 예측 — 72시간 다중 출력. 학습 → 배포 → 대시보드 → 재학습 | **Runway v2.4.0** (최신) | [howto/energy-demand-prediction-v2.4.0/](howto/energy-demand-prediction-v2.4.0/) |
| [energy-demand-prediction](tutorials/energy-demand-prediction/) | 위와 같은 주제의 **이전 판** | Runway v2.2.1 | [howto/energy-demand-prediction-v2.2.1/](howto/energy-demand-prediction-v2.2.1/) |
| [wind-power-prediction-with-xgboost](tutorials/wind-power-prediction-with-xgboost/) | 풍력 발전량 예측 — **단일 출력**. 학습 → 평가 → 배포 | ⚠️ Runway v2.2.0 이전 | [튜토리얼 내 README](tutorials/wind-power-prediction-with-xgboost/README.md) |

주요 기술은 세 튜토리얼 모두 XGBoost · Airflow(KubernetesPodOperator) · MLflow · OpenBao · MinIO(S3)이며,
`energy-demand-prediction-v2`에는 React 대시보드와 Helm 배포가 추가됩니다.

> ⚠️ **`wind-power-prediction-with-xgboost`는 현재 버전 플랫폼에 맞지 않습니다.**
>
> 2026-05-07 이후 갱신되지 않았습니다. 시크릿 주입 방식(OpenBao API 직접 호출)과 의존성 버전
> 처리가 현행 플랫폼과 달라, 그대로 따라 하면 진행되지 않는 단계가 있을 수 있습니다.
> **단일 출력 회귀 예제**로서 유효하므로 남겨 두며, 현행화는 v2.5.0 문서 작업에서 다룹니다.
>
> 지금 처음 시작한다면 **`energy-demand-prediction-v2`** 를 사용하세요.

energy 계열은 튜토리얼 디렉터리에 README를 두지 않고 [`howto/`](howto/)의 단계별 가이드를 정본으로 씁니다.
버전별 폴더를 두는 이유는 [howto/README.md](howto/README.md)를 참고하세요.

## 새 튜토리얼 추가하기

1. `tutorials/_template/` 를 `tutorials/<new-tutorial-name>/` 로 복사
2. `README.md`, `requirements.txt` 를 해당 튜토리얼에 맞게 수정
3. 코드/데이터셋 추가
4. 루트 `README.md`의 튜토리얼 목록에 항목 추가 — **대상 플랫폼 버전을 반드시 함께 적습니다**

## 공통 인프라 (Runway v2)

- **Keycloak**: `keycloak.<your-runway-domain>` — OIDC 토큰 발급
- **MLflow**: 클러스터 내부 URL 접근 + Host 헤더를 `mlflow.<your-runway-domain>` 로 패치
- **PVC**: `/mnt/model-registry` — 모델 아티팩트 영구 저장소
