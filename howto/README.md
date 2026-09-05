# How-to 가이드 사본

Runway 사용자 가이드에 게시된 튜토리얼 본문의 사본입니다. 원본은 `runway-documentation` 저장소의
`docs/tutorial/`이며, 여기에는 **저장소만 받아도 문서를 읽을 수 있도록** 같은 내용을 복사해 둡니다.

## 버전별 폴더

**버전마다 폴더를 따로 두고 이전 버전을 지우지 않습니다.** 옛 가이드를 보고 진행하는 독자가
남아 있기 때문입니다.

**예제 코드도 버전별로 나눠 둡니다.** 예전에는 예제 코드가 한 벌뿐이라 문서만 갈랐지만,
v2.4.0에서 시크릿 주입 방식이 바뀌어 이전 코드와 호환되지 않게 되면서 `tutorials/` 아래에도
버전별 디렉터리를 두기로 했습니다. 문서와 코드는 아래 표의 짝을 반드시 지켜 사용합니다.

| 문서 폴더 | 기준 버전 | 짝이 되는 예제 코드 |
|----------|----------|-------------------|
| [`energy-demand-prediction-v2.4.0/`](energy-demand-prediction-v2.4.0/) | **Runway v2.4.0** (최신) | [`tutorials/energy-demand-prediction-v2/`](../tutorials/energy-demand-prediction-v2/) |
| [`energy-demand-prediction-v2.2.1/`](energy-demand-prediction-v2.2.1/) | Runway v2.2.1 (이전 판) | [`tutorials/energy-demand-prediction/`](../tutorials/energy-demand-prediction/) |

> 디렉터리 이름의 `-v2` 접미사는 **Runway 버전이 아니라 예제 코드의 두 번째 판**이라는 뜻입니다.
> 어느 플랫폼 버전용인지는 이 표로 확인하세요.

각 폴더의 구성은 다음과 같습니다.

```
energy-demand-prediction-v<버전>/
├── how-to-use-energy-demand-tutorial.md   # 전 과정을 한 파일로 이어 붙인 통합본 (AI 어시스턴트용)
├── intro/ 00-preparation/ … appendix/     # 가이드와 같은 단계별 문서
└── assets/                                # 본문에서 참조하는 이미지만
```

## 읽는 순서

단계별 문서는 폴더 이름의 숫자 순서대로 읽습니다 — `intro` → `00-preparation` → `01-dev-env` →
`02-code-data` → `03-training` → `04-inference` → `05-gui` → `06-retrain` → `appendix`.

통합본(`how-to-use-energy-demand-tutorial.md`)은 같은 내용을 한 파일로 이어 붙인 것으로,
각 절 앞에 `<!-- source: <원본 경로> -->` 주석이 붙어 있어 원본 위치를 찾을 수 있습니다.

## 사용자 가이드로 나가는 링크

이 사본은 문서 사이트 밖에 있으므로 **사용자 가이드의 다른 페이지로는 링크를 걸지 않고**,
찾아갈 위치를 경로로 적습니다.

> 사용자 가이드 > 관리하기 > 워크스페이스 관리 > 프로젝트 생성

## 갱신 방법

새 버전 가이드가 확정되면 **기존 폴더는 그대로 두고** 새 폴더를 만듭니다.

1. `runway-documentation`의 `docs/tutorial/`에서 `.md`와 **본문이 참조하는 이미지**를 복사합니다.
2. 사용자 가이드로 나가는 링크를 위와 같이 경로 표기로 바꿉니다.
3. `.pages`의 nav 순서대로 이어 붙여 통합본을 다시 만듭니다.
4. 예제 코드가 이전 버전과 호환되지 않으면 `tutorials/` 아래에 **새 코드 디렉터리를 만듭니다.**
   호환된다면 기존 코드를 그대로 짝으로 씁니다.
5. 이 파일의 표에 새 폴더를 추가하고, 어느 예제 코드와 짝인지 적습니다.
6. 저장소 루트 [`README.md`](../README.md)의 튜토리얼 목록에 **대상 플랫폼 버전과 함께** 추가합니다.
