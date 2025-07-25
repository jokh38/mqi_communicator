## **MQI Communicator: 코드 분석 및 아키텍처 문서**

이 문서는 `mqi_communicator` 프로젝트의 전체적인 구조와 모듈 간의 상호작용을 이해하는 데 도움을 주기 위해 작성되었습니다.

### **Part 1: 전체 프로세스 순서도 (Flowchart)**

아래 순서도는 프로그램의 시작부터 종료까지 일반적인 성공 시나리오를 보여줍니다. 각 단계는 주요 역할을 수행하는 모듈을 함께 표시합니다.

```mermaid
graph TD
    A[Start Program] --> B(Load Config[config_manager]);
    B --> C(Initialize Components[main_controller]);
    C --> D(Acquire Lock & Run Startup Checks[main_controller]);
    D --> E{Scan for New Cases[case_scanner]};
    E -- New Case Found --> F(Queue Case for Processing[main_controller, job_scheduler]);
    F --> G{Check Resources (GPU, Disk)[job_scheduler, gpu_manager]};
    G -- Resources Available --> H(Start Case Processing Thread[main_controller]);
    H --> I(1. Create Workspace[directory_manager]);
    I --> J(2. Upload Data via SFTP[sftp_manager]);
    J --> K(3. Run Remote Interpreter[remote_executor]);
    K --> L(4. Execute Beam Calculations[remote_executor, job_scheduler]);
    L --> M(5. Run DICOM Converter[remote_executor]);
    M --> N(6. Download Results via SFTP[sftp_manager]);
    N --> O(7. Cleanup & Update Status[main_controller, case_scanner]);
    O --> P[End Case Processing];
    
    E -- No New Case --> Q{Wait for Scan Interval[main_controller]};
    Q --> E;
    
    G -- Resources Unavailable --> Q;
    
    subgraph "Error Handling"
        direction LR
        X(Error Occurs) --> Y{Classify Error[error_handler]};
        Y -- Network/GPU Error --> Z(Retry with Backoff[error_handler]);
        Y -- Other Error --> AA(Log Error & Potentially Halt[error_handler, logger]);
    end

    H -- Error --> X;
```

### **Part 2: 세부 모듈 분석**

#### **`main_controller.py`**
*   **개요**: 프로그램의 메인 컨트롤러. 전체 워크플로우를 조율하고, 백그라운드 스레드를 관리하며, 모든 컴포넌트를 초기화하고 연결합니다.
*   **Imports**: `config_manager`, `case_scanner`, `gpu_manager`, `sftp_manager`, `remote_executor`, `job_scheduler`, `process_monitor`, `directory_manager`, `error_handler`, `logger`, `status_display`, `processing_steps`
*   **주요 함수**:
    *   `__init__(config_file:str)`: 모든 관리자 및 핸들러 클래스를 초기화합니다.
    *   `run()`: 메인 실행 루프. 주기적으로 새로운 케이스를 스캔하고 처리 파이프라인을 시작합니다.
    *   `_scan_for_new_cases()`: `case_scanner`를 사용하여 새 케이스를 찾고 큐에 추가합니다.
    *   `_process_case(case_id:str)`: `WorkflowEngine`을 사용하여 단일 케이스의 전체 처리 단계를 실행합니다.
    *   `_background_case_processor()`: 백그라운드에서 케이스 처리 큐를 지속적으로 확인하고 실행하는 스레드 워커입니다.
    *   `shutdown()`: 모든 연결을 종료하고 리소스를 정리합니다.
*   **호출 관계**:
    *   `main_controller` -> `config_manager.get_config()`
    *   `main_controller` -> `case_scanner.scan_for_new_cases()`
    *   `main_controller` -> `job_scheduler.schedule_case()`
    *   `main_controller` -> `processing_steps.WorkflowEngine.execute_workflow()`

#### **`config_manager.py`**
*   **개요**: `config.json` 파일의 로딩 및 접근을 관리합니다.
*   **Imports**: `json`, `pathlib`
*   **주요 함수**:
    *   `_load_config()` -> `Dict[str, Any]`: 설정 파일을 읽어 딕셔너리로 반환합니다.
    *   `get_...( )`: 특정 설정 값을 가져오는 다양한 getter 메서드를 제공합니다.
*   **호출 관계**:
    *   `__init__` -> `_load_config()`
    *   (외부) `main_controller` -> `ConfigManager()` (초기화 시 `_load_config` 호출)

#### **`case_scanner.py`**
*   **개요**: 지정된 디렉토리를 스캔하여 신규 또는 수정된 케이스를 탐지하고, `case_status.json`을 통해 처리 상태를 관리합니다.
*   **Imports**: `json`, `hashlib`, `os`, `pathlib`
*   **주요 함수**:
    *   `scan_for_new_cases()` -> `List[str]`: 새로운 케이스 디렉토리 ID 목록을 반환합니다.
    *   `update_case_status(...)`: 특정 케이스의 상태(예: `NEW`, `PROCESSING`, `COMPLETED`)를 업데이트합니다.
    *   `_load_case_status()` / `_save_case_status()`: 케이스 상태 파일을 읽고 씁니다.
*   **호출 관계**:
    *   `main_controller._scan_for_new_cases()` -> `scan_for_new_cases()`

#### **`job_scheduler.py`**
*   **개요**: 케이스 처리 작업을 스케줄링하고, GPU 및 디스크와 같은 리소스 할당을 관리합니다.
*   **Imports**: `gpu_manager`, `case_scanner`, `remote_executor`
*   **주요 함수**:
    *   `schedule_case(case_id:str)` -> `bool`: 케이스를 처리 대기열에 추가합니다.
    *   `get_next_job()` -> `Optional[Dict]`: 처리 가능한 다음 작업을 큐에서 가져옵니다.
    *   `complete_job(case_id:str, ...)`: 작업 완료 후 리소스를 해제합니다.
    *   `check_remote_disk_space()`: 원격 서버의 디스크 공간을 확인합니다.
*   **호출 관계**:
    *   `main_controller` -> `schedule_case()`
    *   `get_next_job()` -> `gpu_manager.allocate_gpus()`

#### **`remote_executor.py`**
*   **개요**: `base_ssh_connector`를 상속받아 원격 리눅스 서버에 SSH 명령을 실행하는 역할을 특화하여 담당합니다.
*   **Imports**: `paramiko`, `base_ssh_connector`
*   **주요 함수**:
    *   `execute_command(command:str)` -> `Dict`: 원격에서 셸 명령을 실행하고 결과를 반환합니다.
    *   `run_moqui_interpreter(case_id:str)` -> `bool`: 원격에서 MOQUI 파싱 스크립트를 실행합니다.
    *   `run_moqui_beam(...)` -> `bool`: 원격에서 MOQUI 빔 계산 바이너리를 실행합니다.
    *   `run_raw_to_dicom_converter(...)` -> `bool`: 원격에서 DICOM 변환 스크립트를 실행합니다.
*   **호출 관계**:
    *   `processing_steps` -> `run_moqui_interpreter()`, `run_moqui_beam()`, `run_raw_to_dicom_converter()`
    *   `job_scheduler` -> `execute_command()` (디스크 공간 확인 등)

#### **`sftp_manager.py`**
*   **개요**: `base_ssh_connector`를 상속받아 원격 서버와의 SFTP 파일 전송(업로드/다운로드)을 특화하여 담당합니다.
*   **Imports**: `paramiko`, `stat`, `base_ssh_connector`
*   **주요 함수**:
    *   `upload_directory(local_path:str, remote_path:str)` -> `bool`: 로컬 디렉토리를 원격 서버에 재귀적으로 업로드합니다.
    *   `download_directory(remote_path:str, local_path:str)` -> `bool`: 원격 디렉토리를 로컬로 재귀적으로 다운로드합니다.
*   **호출 관계**:
    *   `processing_steps` -> `upload_directory()`, `download_directory()`

#### **`processing_steps.py`**
*   **개요**: 케이스 처리 워크플로우를 개별 단계(Step)로 정의하고, 이를 순차적으로 실행하는 `WorkflowEngine`을 포함합니다.
*   **Imports**: `abc`
*   **주요 클래스**:
    *   `ProcessingStep` (ABC): 모든 처리 단계의 추상 기반 클래스.
    *   `UploadDataStep`, `RunInterpreterStep`, `DownloadResultsStep` 등: 각 처리 단계를 구현한 구체 클래스.
    *   `WorkflowEngine`: 정의된 순서에 따라 각 단계를 실행합니다.
*   **호출 관계**:
    *   `main_controller._process_case()` -> `WorkflowEngine.execute_workflow()`
    *   `WorkflowEngine` -> 각 `ProcessingStep.execute()`
    *   각 `ProcessingStep` -> `sftp_manager`, `remote_executor` 등의 메서드 호출

#### **기타 모듈**
*   **`base_ssh_connector.py`**: SSH/SFTP 연결의 기반 클래스. 연결, 재시도 로직, 연결 해제 등 공통 기능을 제공합니다.
*   **`directory_manager.py`**: 로컬 및 원격 작업 디렉토리 경로를 관리하고 생성합니다.
*   **`gpu_manager.py`**: 원격 서버의 GPU 상태(사용량, 가용성)를 `nvidia-smi` 명령을 통해 확인하고 관리합니다.
*   **`process_monitor.py`**: 원격에서 실행 중인 프로세스를 추적하고 상태를 모니터링합니다.
*   **`error_handler.py`**: 예외를 분류하고, 재시도 로직을 적용하며, 심각한 오류 발생 시 정리 작업을 수행합니다.
*   **`logger.py`**: 파일 및 콘솔에 대한 로깅을 설정하고 관리합니다. 구조화된 로깅을 지원합니다.
*   **`status_display.py`**: 콘솔에 실시간 처리 현황, 시스템 상태, 로그를 시각적으로 표시합니다. `rich` 라이브러리를 사용하여 향상된 UI를 제공합니다.

---
