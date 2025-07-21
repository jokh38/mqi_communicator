# mqi_communicator 워크플로우

`mqi_communicator` 프로젝트는 새로운 데이터(케이스)를 감지하고, 이를 GPU 서버로 안전하게 전송한 뒤, 원격에서 데이터 처리 작업을 실행하는 전체 과정을 자동화합니다.

## 주요 담당 모듈

각 단계에서 중요한 역할을 하는 파이썬 모듈은 다음과 같습니다.

*   **`main_controller.py`**: 전체 프로세스를 지휘하는 오케스트라 지휘자입니다. 주기적으로 새로운 케이스를 확인하고, 작업 스케줄러와 SFTP 매니저 등을 조율하여 전체 흐름을 관리합니다.
*   **`case_scanner.py`**: 지정된 폴더에서 새로운 데이터(케이스)가 들어왔는지 감지하는 역할을 합니다.
*   **`job_scheduler.py`**: 감지된 케이스를 처리할 작업(Job)으로 만들고, GPU 서버의 상태(가용 GPU 등)를 고려하여 언제 작업을 시작할지 결정(스케줄링)합니다.
*   **`sftp_manager.py`**: 실제 SFTP 프로토콜을 사용하여 로컬의 케이스 데이터를 GPU 서버로 전송(업로드)하는 역할을 담당합니다.
*   **`remote_executor.py`**: 데이터 전송이 완료된 후, GPU 서버에서 실제 데이터 처리 스크립트를 실행하라는 명��을 내립니다.
*   **`config.json`**: SFTP 접속 정보(주소, ID, PW), 스캔할 폴더 경로 등 시스템 운영에 필요한 설정값들을 저장합니다.

## 상세 동작 과정 (Step-by-Step)

전체 프로세스는 **"감지 -> 스케줄링 -> 전송 -> 원격 실행"** 의 4단계로 구성됩니다.

### 1단계: 새로운 케이스(데이터) 감지

1.  **시작점 (`main_controller.py`)**:
    *   `MainController`의 메인 루프가 주기적으로 `case_scanner.scan_for_new_cases()` 메서드를 호출합니다.

2.  **스캔 로직 (`case_scanner.py`)**:
    *   `config.json`에 정의된 `source_directory`를 확인합니다.
    *   해당 폴더의 하위 디렉토리 목록을 `case_status.json` 파일과 비교하여 새로운 디렉토리(신규 케이스)를 찾아냅니다.

### 2단계: 작업 스케줄링

1.  **작업 생성 요청 (`main_controller.py`)**:
    *   `MainController`는 발견된 신규 케이스 ID를 `job_scheduler.schedule_case()` 메서드에 전달합니다.

2.  **스케줄링 로직 (`job_scheduler.py`)**:
    *   `schedule_case()`는 케이스를 처리할 수 있는지 확인합니다.
        *   `analyze_case_beam_count`: 케이스 내의 `beam_*.inp` 파일 개수를 파악합니다.
        *   `check_remote_disk_space`: GPU 서버의 디스크 공간이 충분한지 확인합니다.
    *   조건이 충족되면, 새로운 Job 객체를 생성하여 상태를 `PENDING`으로 설정하고 작업 대기열(`job_queue`)에 추가합니다.

### 3단계: 데이터 전송

1.  **작업 시작 (`main_controller.py`)**:
    *   `MainController`는 `job_scheduler.get_next_job()`을 호출하여 대기열에서 실행할 작업을 가져옵니다.
    *   GPU 할당에 성공하면, Job의 상태를 `RUNNING`으로 변경합니다.

2.  **SFTP 전송 실행 (`main_controller.py` -> `sftp_manager.py`)**:
    *   `MainController`는 `sftp_manager.upload_directory()`를 호출하여 데이터 전송을 시작합니다.
    *   로컬 케이스 디렉토리의 모든 내용이 원격 서버의 지정된 경로로 업로드됩니다.

3.  **SFTP 전송 로직 (`sftp_manager.py`)**:
    *   `config.json`의 접속 정보를 사용하여 GPU 서버에 연결하고, 재귀적으로 모든 파일을 업로드합니다.

### 4단계: 원격 처리 실행

1.  **명령 실행 요청 (`main_controller.py` -> `remote_executor.py`)**:
    *   SFTP 전송이 성공적으로 완료되면, `MainController`는 `remote_executor.execute_command()`를 호출합니다.
    *   GPU 서버에서 데이터 처리를 시작하는 쉘 명령어를 전달합니다.

2.  **원격 실행 로직 (`remote_executor.py`)**:
    *   SSH를 통해 GPU 서버에 접속하여 전달받은 명령어를 실행하고, 그 결과를 반환합니다.

## 요약 흐름도

```
[로컬: 신규 데이터(폴더) 생성]
       |
       v
1. CaseScanner.scan_for_new_cases()
   (새로운 폴더 감지)
       |
       v
2. JobScheduler.schedule_case()
   (처리 가능한지 확인 후 작업 대기열에 추가)
       |
       v
3. MainController가 JobScheduler.get_next_job()으로 작업 가져옴
   (GPU 등 리소스 확인 후 작업 시작)
       |
       v
4. SFTPManager.upload_directory()
   (SFTP로 해당 폴더 전체를 GPU 서버로 전송)
       |
       v
5. RemoteExecutor.execute_command()
   (전송 완료 후, GPU 서버에서 처리 스크립트 실행)
       |
       v
[GPU 서버: 데이터 처리 시작]
```
