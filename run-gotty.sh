#!/bin/bash

# Gotty 실행
# --address 0.0.0.0 : 모든 IP에서 접속 허용
# --credential admin:your-password : 보안을 위해 사용자명과 비밀번호 설정
ttyd --address 0.0.0.0 --credential smc:smc1234567 python3 main.py
