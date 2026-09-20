-- 최초 볼륨 생성 시 한 번만 실행된다 (docker-entrypoint-initdb.d).
-- pytest 전용 DB. 실험 DB(app)와 분리한다 (R5).
CREATE DATABASE app_test OWNER app;
