-- Настройка учётной записи для LLM/SQL-агента.
-- Выполнять после db/load_csv.py, под владельцем импортированных таблиц.
-- Пароль в SQL не указываем: задать отдельно через psql \password bi_reader.
\set ON_ERROR_STOP on

SELECT current_database() AS db_name, current_user AS importer_owner \gset

DO $setup$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = 'bi_reader'
          AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
    ) THEN
        RAISE EXCEPTION 'bi_reader already has privileged flags; refusing to continue';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bi_reader') THEN
        CREATE ROLE bi_reader
            LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS NOINHERIT;
    END IF;
END
$setup$;

-- Разрешаем доступ к самой БД и к схеме с данными.
GRANT CONNECT ON DATABASE :"db_name" TO bi_reader;
REVOKE ALL PRIVILEGES ON SCHEMA raw FROM bi_reader;
GRANT USAGE ON SCHEMA raw TO bi_reader;

-- На существующих таблицах оставляем только SELECT.
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA raw FROM bi_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA raw TO bi_reader;

-- Будущие таблицы, созданные этим же пользователем-импортёром.
ALTER DEFAULT PRIVILEGES FOR ROLE :"importer_owner" IN SCHEMA raw
    GRANT SELECT ON TABLES TO bi_reader;

-- Метаданные загрузчика: только чтение таблицы bi_meta.imports.
REVOKE ALL PRIVILEGES ON SCHEMA bi_meta FROM bi_reader;
GRANT USAGE ON SCHEMA bi_meta TO bi_reader;
REVOKE ALL PRIVILEGES ON TABLE bi_meta.imports FROM bi_reader;
GRANT SELECT ON TABLE bi_meta.imports TO bi_reader;

-- Дополнительные ограничения сессий; не замена SQL-валидатору.
-- Клиент может менять некоторые GUC, поэтому права БД — основная защита.
ALTER ROLE bi_reader SET default_transaction_read_only = on;
ALTER ROLE bi_reader SET statement_timeout = '15s';

\echo 'Role bi_reader configured. Set its password interactively: \password bi_reader'
