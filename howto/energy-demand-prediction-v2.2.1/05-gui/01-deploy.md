<!-- v2.2.0 에너지 수요 예측 MLOps 튜토리얼 신규 추가 | 2026-06-16 -->

# 5-1. 웹 대시보드 배포 {#deploy}

에너지 수요 예측 결과를 시각화하고 재학습을 트리거하는 웹 대시보드를 배포합니다.  
튜토리얼에서는 사전에 구성된 Helm 리포지토리를 등록하고 미리 푸시된 컨테이너 이미지를 사용합니다.

!!! note "GUI 이미지·Helm 차트 빌드하는 방법"
    튜토리얼에서는 준비되어 있는 이미지와 Helm 차트를 사용합니다. 직접 Helm 차트를 구성하고, 컨테이너 이미지를 본인 Gitea 레지스트리에 직접 빌드·푸시 해보고 싶다면 :octicons-arrow-right-24: [부록 B](../appendix/b-self-build.md)를 참고하세요.

> 본인 프로젝트 > **애플리케이션** > **+ 생성**

1. **애플리케이션** 메뉴에서 오른쪽 상단 **+ 생성** 버튼을 클릭합니다.

    ![애플리케이션 생성 버튼](../assets/screens/05-01-custom-app-create-01.png)

2. **기본 정보**를 입력합니다.

    - **이름**: 본인이 정하는 이름 (예: `Energy Dashboard`)
    - **ID**: 본인이 정하는 ID (예: `energy-dashboard`)
    - **설명** (선택): 본인이 정하는 설명 (예: `에너지 수요 예측 결과 시각화 및 재학습 트리거`)

    ![기본 정보 입력](../assets/screens/05-01-custom-app-create-02.png)

3. **Helm 리포지토리 URL** 영역 오른쪽에 **등록** 버튼을 클릭합니다.  

    ![리포지토리 등록](../assets/screens/05-01-custom-app-create-02-1.png)

4. **헬름 리포지토리 URL**에 아래 주소를 입력하고, **저장** 버튼을 클릭합니다.

    ```
    https://gitea.try.mrxrunway.ai/api/packages/tutorial-mrx/helm
    ```

    ![차트 등록](../assets/screens/05-01-custom-app-create-03.png)

    !!! note "차트 등록의 의미"
        이 URL은 튜토리얼용으로 사전에 구성된 Helm 리포지토리입니다.  
        **저장**을 클릭하면 Runway가 해당 리포지토리에서 차트 목록을 가져오며, 이후 **차트**·**차트 버전** 드롭다운이 활성화됩니다.

5. **차트**와 **차트 버전**을 선택합니다. 선택하면 하단에 **헬름 차트(values.yaml)**가 표시됩니다.

    - **차트**: `energy-gui`
    - **차트 버전**: `1.1.5`

    ![차트 선택](../assets/screens/05-01-custom-app-create-04.png)


6. **애플리케이션 열기 링크**에서 대시보드 이름과 URL을 추가합니다.

    - **이름**: `Dashboard`
    - **URL**: 본인이 정하는 호스트명 + 도메인 (예: `energy-dashboard.<your-runway-domain>`)

    ![열기 링크 및 values.yaml](../assets/screens/05-01-custom-app-create-05.png)

7. **헬름 차트** 섹션의 `values.yaml`을 수정합니다. `<your-...>` 항목만 본인 환경에 맞게 교체합니다.

    ```yaml
    httpRoute:
      hostname: "<your-gui-hostname>.<your-runway-domain>"

    podAnnotations:
      vault.hashicorp.com/namespace: "<your-project-id>"
      vault.hashicorp.com/role: "<your-openbao-role>"
    ```

    ![values.yaml 수정](../assets/screens/05-01-custom-app-create-06.png)

8. **생성** 버튼을 클릭하고, 상세화면에서 **배포** 버튼을 클릭합니다.

    - 1~2분 뒤 애플리케이션 상태가 **Healthy**로 바뀌면 완료입니다.

---

:octicons-arrow-right-24: 다음 단계: **[5-2. 대시보드 접속 및 설정](02-setup.md)**
