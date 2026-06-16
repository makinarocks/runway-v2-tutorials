# Energy Demand Prediction — 남은 작업 정리

히스토리 기반으로 정리한 전체 작업 목록.
STEPS.md 는 최종 튜토리얼 문서이고, 이 파일은 **작업 트래킹용**.

---

## A. 이미 완료된 것

- [x] Gitea 레포 생성 + 소스 코드 push
- [x] Actions Secrets 등록 (GIT_USERNAME, GIT_TOKEN, IMAGE_TAG, GUI_IMAGE_TAG)
- [x] CI/CD 워크플로우 동작 확인 (ML 이미지, GUI 이미지, DAG sync)
- [x] PVC 생성 (`<your-pvc-name>`, ceph-filesystem, RWX)
- [x] PVC 에 데이터셋 업로드 (pred-demo-dataset, pred-demo-testset)
- [x] OpenBao `secret/energy-demand` 시크릿 등록 (5개 키)
- [x] RoleBinding 확인 (wind-power 에서 이미 생성)
- [x] Code Server 에서 파이프라인 수동 실행 성공 (load_data → train_model → evaluate_model → log_to_mlflow)
- [x] MLflow 모델 등록 확인
- [x] download_model.py 로 PVC 에 모델 아티팩트 복사
- [x] 추론 엔드포인트 생성 + 모델 배포 (MLServer pyfunc)
- [x] test_inference.py 로 추론 테스트 성공 (72개 출력)
- [x] GUI Helm chart 패키징 + Gitea 레지스트리 업로드
- [x] GUI Runway 배포 (HTTPRoute parentRefs 수정 포함)
- [x] 로컬 GUI 에서 추론 호출 성공 (Vite 프록시)
- [x] 로컬 GUI 에서 실측 데이터 업로드 + 차트 비교 동작
- [x] Airflow v2 API DAG trigger 방식 확인 (Keycloak 토큰 + logical_date 필수)
- [x] nginx 프록시 설정 (추론 + Airflow CORS 해결)

---

## B. 아직 해야 하는 것

### B-1. GUI Runway 배포 최종 검증

- [ ] Helm chart 0.4.0 업로드 완료 확인
- [ ] Runway 콘솔에서 chart 버전 0.4.0 으로 변경 + Sync
- [ ] `https://<your-gui-hostname>.<your-runway-domain>` 접속 → React 앱 로드 확인
- [ ] Runway 배포 GUI 에서 추론 테스트 (nginx 프록시 경유)
  - 추론 엔드포인트: `/api/inference/<your-project-id>/energy-demand-prediction/energy-demand-pred`
  - Deployment ID: `default`
- [ ] Runway 배포 GUI 에서 실측 데이터 업로드 → 차트에 실측 라인 표시 확인
- [ ] Runway 배포 GUI 에서 재학습 버튼 → Airflow DAG trigger 확인
  - Airflow URL: `/api/airflow`
  - Airflow 토큰: 브라우저 DevTools 에서 복사
  - DAG ID: `energy_demand_prediction_<your-project-id>`

### B-2. 재학습 후 모델 교체

모델 B (Q1+Q2+Q3 학습)를 만들어서 재학습 후 엔드포인트를 교체하는 흐름 검증.

- [ ] Code Server에서 Q1+Q2+Q3 재학습 실행
  ```bash
  source venv/bin/activate
  TRAIN_FILES="Q1.csv,Q2.csv,Q3.csv" python task_runner.py --step load_data
  python task_runner.py --step train_model
  python task_runner.py --step evaluate_model
  python task_runner.py --step log_to_mlflow
  ```
- [ ] MLflow에서 새 모델 버전 확인
- [ ] 새 모델 아티팩트 PVC에 다운로드
  ```bash
  python download_model.py
  ls /mnt/data/models/  # 새 m-xxx 디렉토리 확인
  ```
- [ ] Runway 콘솔에서 기존 엔드포인트의 모델 경로를 새 모델로 변경 (또는 새 배포 생성)
- [ ] 추론 테스트 → Q1만 학습 대비 정확도 개선 확인

### B-3. 데모용 모델 2개 사전 배포

데모 시연 시 재학습에 시간이 오래 걸리므로, 모델 A/B를 미리 배포해두고
재학습 버튼 클릭 후 엔드포인트만 전환하여 즉시 성능 차이를 보여주는 구성.

**준비:**
- [ ] 모델 A (Q1만 학습) — 이미 학습/배포 완료
  - 엔드포인트: `energy-demand-prediction` / 배포: `energy-demand-pred`
  - 모델 경로: `/mnt/data/models/m-<모델A-id>`
- [ ] 모델 B (Q1+Q2+Q3 학습) — B-2 에서 생성
  - 엔드포인트: `energy-demand-prediction` / 배포: `energy-demand-retrained` (새로 생성)
  - 모델 경로: `/mnt/data/models/m-<모델B-id>`

**GUI 수정:**
- [ ] `apiSettings`에 `retrainedEndpoint` 필드 추가 (재학습 후 전환할 엔드포인트 URL)
- [ ] `handleRetraining`에서 DAG trigger 후 → `inferenceEndpoint`를 `retrainedEndpoint`로 교체
- [ ] 이후 추론 요청이 모델 B 엔드포인트로 전송됨

**데모 흐름:**
1. GUI에서 모델 A 엔드포인트로 추론 → 낮은 정확도
2. 실측 데이터 업로드 → 메트릭 확인 (MAPE 높음)
3. 재학습 버튼 클릭 → Airflow DAG trigger + 엔드포인트 모델 B로 전환
4. 재추론 → 정확도 개선된 결과 즉시 확인

### B-3. STEPS.md 최종 고도화

히스토리에서 발견된 이슈들을 STEPS.md 에 반영:

- [ ] Python 3.10 설치 + setup.sh 사용법 명시
- [ ] PVC 이름/접근 모드 주의사항 (RWX 필수, 이름 일치)
- [ ] OpenBao 토큰 갱신 절차 간소화 안내
- [ ] Helm chart 패키징 + Gitea 업로드 절차
- [ ] HTTPRoute parentRefs 필수 설정 안내
- [ ] Airflow 3.0 v2 API + Keycloak 토큰 사용법
- [ ] 리소스 쿼터 주의사항 (CPU 10코어 제한)
- [ ] GUI API 설정 값 안내 (로컬 vs Runway)

### B-4. 코드 정리

- [ ] DEBUG 로그 제거 (`console.log('[DEBUG]...')`)
- [ ] GUI 커밋 + push + Helm chart 최종 빌드
- [ ] Dockerfile Python 버전 주석 정리

---

## C. 알려진 이슈 / 플랫폼 피드백

| 이슈 | 상태 | 대응 |
|------|------|------|
| OpenBao 토큰 짧은 만료 주기 | 미해결 | Agent Injector 또는 long-lived 토큰 필요. Runway 팀 전달 예정 |
| 커스텀 Helm HTTPRoute parentRefs 미문서화 | 미해결 | Runway 팀에 문서화 요청 |
| Airflow 3.0 v2 API + Keycloak OIDC 인증 | 확인됨 | Runway API Key(offline token) 사용, v2 + logical_date 필수 |
| MLServer Python 3.10 pickle 호환성 | 해결 | Dockerfile 을 3.10 으로 맞춤 |
| PVC RWO → Multi-Attach 에러 | 해결 | ceph-filesystem + RWX 로 재생성 |
| CPU 쿼터 초과 (10코어 제한) | 해결 | Code Server 리소스 조정 또는 수동 실행 |
