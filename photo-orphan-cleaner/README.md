# Photo Orphan Cleaner (짝 없는 사진 정리)

사진 뷰어·편집 프로그램에서 잘못 찍힌 사진을 지우면 보통 **한 확장자**(예: JPG)만 지워지고,
같은 사진을 다른 형식으로 저장한 사본(예: ARW RAW, TIFF, HEIF)은 그대로 남습니다.
이 도구는 정리를 끝낸 **레퍼런스 폴더**를 기준으로, **대상 폴더**에서 같은 이름의 짝이 없는
사진을 찾아 목록으로 보여 주고, 확인을 받은 뒤 **Windows 휴지통으로 이동**합니다(영구 삭제하지 않음).

## 사용법

1. **레퍼런스 폴더**와 **레퍼런스 확장자**(하나)를 고릅니다. → 이 폴더에 남아 있는 사진이 "남길 사진"의 기준입니다.
2. **대상 폴더**와 **대상 확장자**(여러 개 가능, 기본값: 전체 선택)를 고릅니다.
3. 필요하면 각 폴더의 **하위 폴더 포함**을 켭니다(기본값: 꺼짐).
4. **[검색]** → 레퍼런스와 짝이 없는 대상 사진 목록이 표시됩니다.
   - 더블클릭으로 파일을 열어 볼 수 있고, 남기고 싶은 파일은 **[선택 항목 제외]**(또는 Delete 키)로 목록에서 뺄 수 있습니다.
5. **[목록 전체를 휴지통으로 이동…]** → 확인 창에서 "예"를 누르면 목록의 파일이 휴지통으로 이동합니다.

예: 레퍼런스(JPG)에 `DSC01001.JPG`, `DSC01002.JPG`만 남아 있고 대상 폴더에
`DSC01001.ARW`, `DSC01002.ARW`, `DSC01003.ARW`, `DSC01003.HIF`가 있으면
`DSC01003.ARW`, `DSC01003.HIF`가 휴지통 이동 대상이 됩니다.

### 지원 확장자

| 옵션 | 포함되는 확장자 |
| --- | --- |
| TIFF | `.tif`, `.tiff` |
| ARW | `.arw` |
| JPG | `.jpg`, `.jpeg` |
| HEIF | `.heif`, `.heic`, `.hif` (소니 등 카메라는 HEIF를 `.HIF`로 저장) |

### 이름 비교 규칙

- **확장자를 뺀 파일 이름**이 완전히 같을 때만 짝으로 봅니다. 대소문자는 구분하지 않습니다(`dsc0001.jpg` ↔ `DSC0001.ARW`).
- `DSC0001-Edit.jpg`처럼 이름이 바뀐 파일은 `DSC0001.ARW`의 짝이 **아닙니다**. 편집 프로그램이 이름에 접미사를 붙여 내보낸 경우 주의하세요.
- 하위 폴더 포함 시 폴더 위치는 따지지 않고 이름만 비교합니다. 레퍼런스 어느 하위 폴더에든 같은 이름이 있으면 남깁니다.

### 안전장치

- 파일은 항상 **휴지통**으로만 이동합니다(`SHFileOperationW` + `FOF_ALLOWUNDO`, 탐색기의 "삭제"와 같은 동작).
- 휴지통이 없는 드라이브(USB 메모리, SD 카드, 네트워크 드라이브 등)에 있는 파일이 목록에 있으면 **작업 전체를 거부**합니다. 사진을 PC 로컬 디스크로 복사한 뒤 사용하세요.
- 휴지통이 꺼져 있거나 파일이 휴지통 용량보다 커서 Windows가 영구 삭제하려 하면 Windows 확인 창이 뜹니다. "아니요"를 누르면 그 파일과 나머지 파일은 그대로 남습니다.
- 레퍼런스 폴더에서 선택한 확장자의 사진을 하나도 찾지 못하면 검색을 중단합니다(대상 전체가 삭제 대상이 되는 실수 방지).
- 검색 후 폴더·확장자·옵션을 바꾸면 이전 결과는 자동으로 지워집니다.
- 지우지 못한 파일(사용 중, 경로 260자 초과 등)은 목록에 남고 이유가 표시됩니다.

## 실행 / 빌드

### 실행 파일(.exe)

GitHub Actions의 `photo-orphan-cleaner` 워크플로가 Windows에서 테스트 후
`PhotoOrphanCleaner.exe`(단일 파일, 설치 불필요)를 빌드해 artifact로 올립니다.
Actions 탭 → 최근 실행 → **PhotoOrphanCleaner-windows** 를 내려받으세요.

> 서명되지 않은 exe라 처음 실행할 때 Windows SmartScreen 경고가 뜰 수 있습니다("추가 정보 → 실행").

### 직접 빌드

[python.org](https://www.python.org/downloads/windows/)의 Python 3.10 이상(기본 설치에 tkinter 포함)을 설치한 뒤:

```bat
cd photo-orphan-cleaner
build.bat
```

`dist\PhotoOrphanCleaner.exe`가 만들어집니다.

### Python으로 바로 실행

```bat
cd photo-orphan-cleaner
pythonw PhotoOrphanCleaner.pyw
```

(또는 `PhotoOrphanCleaner.pyw`를 더블클릭) 외부 라이브러리는 필요 없습니다(표준 라이브러리만 사용).

## 테스트

```bat
python -m unittest discover -s tests -v
```

Windows에서는 실제 휴지통 이동 테스트도 함께 실행됩니다.

## 구조

```
photo_orphan_cleaner/
  core.py     # 폴더 탐색과 이름 매칭 (GUI·OS 의존성 없음)
  recycle.py  # Windows 휴지통 이동, 휴지통 없는 드라이브 감지
  app.py      # tkinter GUI
PhotoOrphanCleaner.pyw  # 실행 진입점 (PyInstaller 입력)
```
