
# 1. MQI Communicator 시스템 소개

## 몬테카를로 방사선 치료 시뮬레이션 워크플로우 관리 시스템

| 구분 | 내용 |
| :--- | :--- |
| **발표 주제** | MQI Communicator 시스템 소개 |
| **목표** | 복잡한 HPC(고성능 컴퓨팅) 시뮬레이션 전 과정을 자동화 및 관리 |

---

# 2. 프로젝트 개요: 시스템의 역할과 목표 (Project Overview)

## MQI Communicator는 왜 필요한가요?

* **문제**: 방사선 치료 계획 수립 시, 정확도를 높이기 위한 **몬테카를로 시뮬레이션**은 ①데이터 변환, ②HPC 전송, ③장시간 실행 및 감시, ④결과 변환 등 **여러 단계를 수동**으로 거쳐야 합니다.
* **목표**: 이러한 복잡하고 오류 발생 가능성이 높은 전 과정을 **자동화**하고, **병렬 처리**하여 처리 속도와 신뢰성을 높이는 관리 시스템을 구축하는 것입니다.

## 시스템의 주요 역할
| 입력 (In) | 처리 (Process) | 출력 (Out) |
| :---: | :---: | :---: |
| RTPLAN, 장비 로그 | **MQI Communicator** (자동화 및 관리) | Monte Carlo Raw Dose |
| | **(Workflow Management)** | DICOM RT Dose (최종 결과) |

---

# 3. 시스템 아키텍처: 주요 구성 요소 (Architecture)

## 중앙 집중형 관리 시스템 (Coordinator)

| 구성 요소 | 역할 (비유) | 상세 설명 |
| :---: | :--- | :--- |
| **Main App** | **사령탑** | 전체 서비스 시작/종료 및 Case 처리 순서 결정. |
| **Worker Pool** | **병렬 일꾼** | 각 Beam(빔)별 시뮬레이션을 독립적으로 수행하는 프로세스 그룹. |
| **Execution Handler** | **실행 담당자** | 모든 명령(SSH, 로컬 실행, 파일 전송)을 **Local/Remote** 모드로 통일하여 처리. |
| **Case Repository** | **기억 장치** | 모든 Case 및 Beam의 **상태, 진행률, 오류 정보**를 저장 (SQLite DB). |
| **GPU Monitor** | **자원 감시관** | 연결된 GPU의 **실시간 사용 현황**을 주기적으로 수집 및 업데이트. |

---

# 4. 데이터 흐름: Case 처리 개요 (Data Flow)

## Case 처리 파이프라인 (Case Processing Pipeline)
1.  **Case 탐지**: 파일 감시 시스템이 새 환자 폴더를 발견하고 대기열(Queue)에 추가합니다.
2.  **Case 준비**: Case 단위로 데이터 무결성 검사 및 CSV 파일 변환(Interpretation)을 수행합니다.
3.  **GPU 할당**: 사용 가능한 GPU를 찾고, Beam별로 GPU를 할당하여 **TPS 설정 파일**을 생성합니다.
4.  **Worker Dispatch**: 할당된 GPU를 가진 Beam들을 독립적인 **Worker Process**로 실행합니다.
5.  **Beam Workflow**: 각 Worker는 **State Machine**을 따라 HPC 시뮬레이션 과정을 진행합니다.
6.  **결과 통합**: 모든 Beam이 완료되면, 최종적으로 Case 상태를 `COMPLETED`로 결정합니다.

---

# 5. Case 처리 단계 (Workflow 1): 준비 및 해석

## Step 1: 초기 검증 (`InitialState`)
* **Case Path 검증**: 대상 Case 폴더의 존재 여부를 확인합니다.
* **Beam 데이터 검증**: DICOM RT Plan을 분석하여 **예상 Beam 개수**와 **실제 데이터 폴더** 개수가 일치하는지 확인하여 데이터 전송 무결성을 검증합니다.

## Step 2: CSV 해석 (`CSV_INTERPRETING`)
* **목적**: DICOM 및 로그 파일의 데이터를 **MQI Interpreter**를 통해 Monte Carlo 시뮬레이션용 CSV 파일로 변환합니다.
* **실행**: `ExecutionHandler`가 `mqi_interpreter/main_cli.py` 스크립트를 로컬에서 실행하며, 결과 CSV 파일과 DICOM 파일을 후속 처리를 위해 지정된 출력 디렉토리에 저장합니다.

---

# 6. Case 처리 단계 (Workflow 2): 시뮬레이션 환경 구성

## Step 3: GPU 할당 및 TPS 파일 생성 (`TPS_GENERATION`)
* **GPU 할당**: `GpuRepository`를 통해 현재 사용 가능한 **GPU**를 찾고, 해당 GPU의 고유 **ID**와 **Index**를 Beam에 할당합니다.
* **TPS 파일 생성**: `TpsGenerator`가 할당된 **GPU ID**와 Case/Beam 정보를 기반으로 시뮬레이션 설정 파일(`moqui_tps.in`)을 생성합니다.
    * **특징**: 각 Beam은 자신만의 GPU 할당 정보가 포함된 TPS 파일을 가집니다.

## Step 4: 파일 업로드 (`FileUploadState`)
* **조건**: `config.yaml`에 설정된 `ExecutionHandler` 모드가 **`remote`**일 경우에만 실행됩니다.
* **실행**: `ExecutionHandler`가 **SSH/SFTP** 연결을 통해 TPS 설정 파일을 HPC 클러스터의 원격 작업 폴더로 안전하게 전송합니다.

---

# 7. Case 처리 단계 (Workflow 3): HPC 실행 및 모니터링

## Step 5: HPC 실행 (`HpcExecutionState`)
* **원격 모드**: HPC 스케줄러 명령(예: `sbatch`)을 실행하여 시뮬레이션 Job을 제출하고 **Job ID**를 할당받습니다.
* **로컬 모드**: 시뮬레이션 실행 명령을 로컬에서 직접 실행합니다.

## 실시간 진행률 모니터링 (Progress Tracking)
* **방법**: HPC에서 생성되는 로그 파일을 **실시간으로 읽어(Tailing)** 특정 패턴(`Generating particles for (X of Y batches)`)을 탐지합니다.
* **업데이트**: 탐지된 현재 Batch 수(X)와 전체 Batch 수(Y)를 기반으로 진행률을 계산하고, DB의 Beam 레코드(`progress` 필드)에 **실시간**으로 반영합니다.

---

# 8. Case 처리 단계 (Workflow 4): 결과 처리 및 종료

## Step 6: 결과 다운로드 (`DownloadState`)
* **실행**: `ExecutionHandler`가 HPC에 저장된 최종 Raw 데이터 파일(`beam_N.raw`)을 로컬(`simulation_output_dir`)로 다운로드합니다.
* **후처리**: 다운로드가 완료되면, 원격지 HPC의 임시 작업 폴더를 정리(`rm -rf`)합니다.

## Step 7: 후처리 (`PostprocessingState`)
* **실행**: Monte Carlo Raw Dose 데이터를 DICOM RT Dose 파일로 변환하는 `RawToDCM` 스크립트를 로컬에서 실행합니다.

## Step 8: 최종 결과 전송 및 완료 (`CompletedState`)
* **최종 전송**: 변환된 최종 DICOM 결과 파일들을 **`PC_localdata`** 서버로 전송합니다.
* **종료**: 모든 Beam이 완료되면, `Case Aggregator`가 Case의 상태를 `COMPLETED`로 최종 업데이트합니다.

---

# 9. 주요 기술적 특징 (Key Features)

## 1. 유연한 실행 환경 및 원격 제어
* **ExecutionHandler**: 모든 명령(실행, 전송)을 **Local(로컬 시스템)** 또는 **Remote(SSH/SFTP)** 환경에서 동일하게 처리할 수 있도록 추상화되었습니다.
* **설정**: `config.yaml` 파일에서 각 단계별(예: `GpuMonitor`, `HpcJobSubmitter`) 실행 모드를 유연하게 선택 가능합니다.

## 2. 실시간 모니터링 대시보드
* **기능**: 별도의 프로세스로 실행되는 **대시보드 UI**를 통해 Case 상태, Beam 진행률, **GPU 자원 사용 현황**을 실시간으로 확인할 수 있습니다.

## 3. 병렬 처리 및 자원 관리
* **Process Pool**: `ProcessPoolExecutor`를 활용하여 여러 Beam 시뮬레이션을 **병렬**로 실행합니다.
* **GPU 관리**: `GpuRepository`는 **경쟁 조건(Race Condition)**을 방지하며 가용한 GPU를 탐색하고, Beam에 **원자적(Atomic)**으로 할당/해제합니다.

---

# 10. 결론 (Summary)

## MQI Communicator의 최종 목표

| 핵심 기능 | 설명 |
| :---: | :--- |
| **자동화된 워크플로우** | Case 탐지부터 최종 결과 전송까지 **전 과정을 자동화**하여 사용자 개입을 최소화. |
| **HPC 연동 및 모니터링** | HPC 자원을 효율적으로 활용하고, **실시간 진행률** 및 **GPU 사용량**을 투명하게 모니터링. |
| **견고한 시스템 구조** | **State Pattern** 및 **Helper Methods**를 도입하여 코드의 응집도를 높이고 유지보수성을 극대화. |

## 최종 결론
MQI Communicator는 **병렬 처리, 자원 관리, 원격 제어** 기능을 통합하여 방사선 치료 계획 시뮬레이션의 **효율성과 신뢰성**을 극대화하는 **엔드-투-엔드(End-to-End)** 관리 시스템입니다.