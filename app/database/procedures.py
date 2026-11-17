"""PostgreSQL stored procedures (usp_*) backing the CAFirm API layer.

Every API endpoint maps 1:1 to one named procedure in this module. The same
statements are executed by the Alembic migration (production) and by the test
fixtures (cafarm_test database), so this module is the single source of truth.

Naming convention (professional & self-identifying):
    usp_<action>_<object>          e.g. usp_submit_visitor_enquiry
    usp_get_*                      read operations
    usp_list_*                     paginated collections
    usp_create_/usp_submit_        insert operations
    usp_report_*                   the eight report types

All procedures return a uniform jsonb envelope:
    {"success": true,  "data": {...}}
    {"success": false, "code": "VALIDATION|NOT_FOUND|FORBIDDEN|CONFLICT|ERROR", "error": "message"}
"""
import re

from sqlalchemy import text

PGCRYPTO_SQL = "CREATE EXTENSION IF NOT EXISTS pgcrypto;"
SEQUENCE_SQL = "CREATE SEQUENCE IF NOT EXISTS enquiry_number_seq START 1;"

# The ORM models stamp timestamps in Python, so the columns carry no database
# default. Procedures write rows directly, therefore we install database-level
# defaults here to keep every insert correct.
TIMESTAMP_DEFAULTS_SQL = [
    "ALTER TABLE enquiries ALTER COLUMN created_at SET DEFAULT now();",
    "ALTER TABLE enquiries ALTER COLUMN updated_at SET DEFAULT now();",
    "ALTER TABLE users ALTER COLUMN created_at SET DEFAULT now();",
    "ALTER TABLE users ALTER COLUMN updated_at SET DEFAULT now();",
    "ALTER TABLE roles ALTER COLUMN created_at SET DEFAULT now();",
    "ALTER TABLE enquiry_categories ALTER COLUMN created_at SET DEFAULT now();",
    "ALTER TABLE enquiry_status_history ALTER COLUMN changed_at SET DEFAULT now();",
    "ALTER TABLE enquiry_assignments ALTER COLUMN assigned_at SET DEFAULT now();",
    "ALTER TABLE enquiry_attachments ALTER COLUMN created_at SET DEFAULT now();",
    "ALTER TABLE activity_logs ALTER COLUMN created_at SET DEFAULT now();",
]

# --------------------------------------------------------------------------
# 1. Envelope helpers + shared utilities
# --------------------------------------------------------------------------
HELPER_PROCEDURES = [
    r'''
    CREATE OR REPLACE FUNCTION usp_api_success(p_data jsonb DEFAULT NULL)
    RETURNS jsonb LANGUAGE sql AS $$
        SELECT jsonb_build_object('success', true, 'data', p_data);
    $$;
    ''',
    r'''
    CREATE OR REPLACE FUNCTION usp_api_error(p_code text, p_message text)
    RETURNS jsonb LANGUAGE sql AS $$
        SELECT jsonb_build_object('success', false, 'code', p_code, 'error', p_message);
    $$;
    ''',
    r'''
    CREATE OR REPLACE FUNCTION usp_normalize_mobile(p_mobile text)
    RETURNS text LANGUAGE sql AS $$
        SELECT regexp_replace(coalesce(p_mobile, ''), '[^0-9+]', '', 'g');
    $$;
    ''',
    # Keep updated_at correct when procedures update rows directly.
    r'''
    CREATE OR REPLACE FUNCTION usp_set_updated_at()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        NEW.updated_at := now();
        RETURN NEW;
    END $$;
    ''',
    "DROP TRIGGER IF EXISTS trg_enquiries_updated_at ON enquiries;",
    """
    CREATE TRIGGER trg_enquiries_updated_at BEFORE UPDATE ON enquiries
    FOR EACH ROW EXECUTE FUNCTION usp_set_updated_at();
    """,
    "DROP TRIGGER IF EXISTS trg_users_updated_at ON users;",
    """
    CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION usp_set_updated_at();
    """,
]

# --------------------------------------------------------------------------
# 2. Public / visitor procedures
# --------------------------------------------------------------------------
PUBLIC_PROCEDURES = [
    # -- Submit a visitor enquiry (returns the Enquiry Number) ---------------
    r'''
    CREATE OR REPLACE FUNCTION usp_submit_visitor_enquiry(
        p_full_name text, p_mobile_number text, p_category_id integer,
        p_subject text, p_description text,
        p_email text DEFAULT NULL, p_state text DEFAULT NULL,
        p_district text DEFAULT NULL, p_ip_address text DEFAULT NULL
    ) RETURNS jsonb LANGUAGE plpgsql AS $$
    DECLARE
        v_category_id integer;
        v_enquiry_id integer;
        v_enquiry_number text;
    BEGIN
        p_full_name := btrim(coalesce(p_full_name, ''));
        p_subject := btrim(coalesce(p_subject, ''));
        p_description := btrim(coalesce(p_description, ''));

        IF length(p_full_name) < 2 OR length(p_full_name) > 100 THEN
            RETURN usp_api_error('VALIDATION', 'Full name must be between 2 and 100 characters.');
        END IF;
        p_mobile_number := usp_normalize_mobile(p_mobile_number);
        IF p_mobile_number !~ '^\+?[0-9]{10,15}$' THEN
            RETURN usp_api_error('VALIDATION',
                'Mobile number must contain 10 to 15 digits (a leading + is allowed).');
        END IF;
        IF p_email IS NOT NULL AND btrim(p_email) <> '' THEN
            p_email := btrim(p_email);
            IF length(p_email) > 120 OR p_email !~ '^[^@\s]+@[^@\s]+\.[^@\s]{2,}$' THEN
                RETURN usp_api_error('VALIDATION', 'Please enter a valid email address.');
            END IF;
        ELSE
            p_email := NULL;
        END IF;
        p_state := left(NULLIF(btrim(coalesce(p_state, '')), ''), 50);
        p_district := left(NULLIF(btrim(coalesce(p_district, '')), ''), 50);

        IF p_category_id IS NULL THEN
            RETURN usp_api_error('VALIDATION', 'Please select an enquiry category.');
        END IF;
        SELECT category_id INTO v_category_id FROM enquiry_categories
        WHERE category_id = p_category_id AND is_active;
        IF v_category_id IS NULL THEN
            RETURN usp_api_error('VALIDATION', 'Selected enquiry category is not available.');
        END IF;
        IF length(p_subject) < 5 OR length(p_subject) > 150 THEN
            RETURN usp_api_error('VALIDATION', 'Subject must be between 5 and 150 characters.');
        END IF;
        IF length(p_description) < 10 OR length(p_description) > 5000 THEN
            RETURN usp_api_error('VALIDATION', 'Description must be between 10 and 5000 characters.');
        END IF;

        v_enquiry_number := 'CAF-' || to_char(now(), 'YYYY') || '-'
                            || lpad(nextval('enquiry_number_seq')::text, 6, '0');
        INSERT INTO enquiries (enquiry_number, full_name, mobile_number, email, state, district,
                               category_id, subject, description, source, status)
        VALUES (v_enquiry_number, p_full_name, p_mobile_number, p_email, p_state, p_district,
                p_category_id, p_subject, p_description, 'VISITOR', 'New')
        RETURNING enquiry_id INTO v_enquiry_id;

        INSERT INTO enquiry_status_history (enquiry_id, previous_status, new_status, remarks)
        VALUES (v_enquiry_id, NULL, 'New', 'Enquiry created');

        INSERT INTO activity_logs (action, description, enquiry_id, ip_address)
        VALUES ('ENQUIRY_CREATED', 'Enquiry ' || v_enquiry_number || ' created (VISITOR)',
                v_enquiry_id, p_ip_address);

        RETURN usp_api_success(jsonb_build_object(
            'enquiry_id', v_enquiry_id,
            'enquiry_number', v_enquiry_number,
            'status', 'New',
            'tracking_note', 'Track anytime with this Enquiry Number or the registered mobile number.'));
    END;
    $$;
    ''',
    # -- Track status with the enquiry reference number (tokenless) ----------
    r'''
    CREATE OR REPLACE FUNCTION usp_track_status_by_reference(
        p_enquiry_number text, p_ip_address text DEFAULT NULL
    ) RETURNS jsonb LANGUAGE plpgsql AS $$
    DECLARE
        v_enquiry enquiries%ROWTYPE;
        v_history jsonb;
    BEGIN
        p_enquiry_number := btrim(coalesce(p_enquiry_number, ''));
        IF p_enquiry_number = '' THEN
            RETURN usp_api_error('VALIDATION', 'Enter your enquiry reference number.');
        END IF;
        SELECT * INTO v_enquiry FROM enquiries
        WHERE upper(enquiry_number) = upper(p_enquiry_number) LIMIT 1;
        IF NOT FOUND THEN
            RETURN usp_api_error('NOT_FOUND',
                'No matching enquiry found. Please check the enquiry reference number.');
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'status', h.new_status, 'changed_at', h.changed_at)
                   ORDER BY h.changed_at, h.history_id), '[]'::jsonb)
          INTO v_history
          FROM enquiry_status_history h
         WHERE h.enquiry_id = v_enquiry.enquiry_id
           AND h.new_status IS DISTINCT FROM h.previous_status;
        INSERT INTO activity_logs (action, description, enquiry_id, ip_address)
        VALUES ('VISITOR_TRACKED',
                'Enquiry ' || v_enquiry.enquiry_number || ' status viewed by visitor',
                v_enquiry.enquiry_id, p_ip_address);
        RETURN usp_api_success(jsonb_build_object(
            'enquiry', jsonb_build_object(
                'enquiry_number', v_enquiry.enquiry_number,
                'subject', v_enquiry.subject,
                'status', v_enquiry.status,
                'category', (SELECT c.category_name FROM enquiry_categories c
                             WHERE c.category_id = v_enquiry.category_id),
                'assigned_to', (
                    SELECT jsonb_build_object('name', u.full_name, 'email', u.email)
                    FROM users u
                    WHERE u.user_id = v_enquiry.assigned_to_user_id
                ),
                'created_at', v_enquiry.created_at,
                'updated_at', v_enquiry.updated_at),
            'history', v_history));
    END;
    $$;
    ''',
    # -- Look up enquiries by mobile number (any-one tracking) ----------------
    r'''
    CREATE OR REPLACE FUNCTION usp_lookup_enquiries_by_mobile(
        p_mobile text, p_limit integer DEFAULT 10
    ) RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
    DECLARE
        v_mobile text;
        v_count integer;
    BEGIN
        v_mobile := usp_normalize_mobile(p_mobile);
        IF v_mobile !~ '^\+?[0-9]{10,15}$' THEN
            RETURN usp_api_error('VALIDATION', 'Enter a valid mobile number (10 to 15 digits).');
        END IF;
        SELECT count(*) INTO v_count FROM enquiries e
        WHERE usp_normalize_mobile(e.mobile_number) = v_mobile;
        IF v_count = 0 THEN
            RETURN usp_api_error('NOT_FOUND', 'No enquiries were found for this mobile number.');
        END IF;
        RETURN usp_api_success(jsonb_build_object(
            'total', v_count,
            'items', (SELECT COALESCE(jsonb_agg(x ORDER BY x.created_at DESC), '[]'::jsonb) FROM (
                SELECT e.enquiry_number, e.subject, e.status, c.category_name,
                       e.created_at, e.updated_at
                FROM enquiries e
                LEFT JOIN enquiry_categories c ON c.category_id = e.category_id
                WHERE usp_normalize_mobile(e.mobile_number) = v_mobile
                ORDER BY e.created_at DESC
                LIMIT LEAST(GREATEST(coalesce(p_limit, 10), 1), 50)) x)));
    END;
    $$;
    ''',
    # -- Public landing-page statistics --------------------------------------
    r'''
    CREATE OR REPLACE FUNCTION usp_get_public_stats()
    RETURNS jsonb LANGUAGE sql STABLE AS $$
        SELECT usp_api_success(jsonb_build_object(
            'total_enquiries', (SELECT count(*) FROM enquiries),
            'resolved_enquiries', (SELECT count(*) FROM enquiries WHERE status = 'Resolved'),
            'pending_enquiries', (SELECT count(*) FROM enquiries WHERE status IN
                ('New', 'Pending', 'Assigned', 'In Progress', 'Reopened')),
            'active_categories', (SELECT count(*) FROM enquiry_categories WHERE is_active),
            'categories', (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'category_id', c.category_id, 'category_name', c.category_name,
                'description', c.description) ORDER BY c.category_name), '[]'::jsonb)
                FROM enquiry_categories c WHERE c.is_active)));
    $$;
    ''',
    r'''
    CREATE OR REPLACE FUNCTION usp_list_active_categories()
    RETURNS jsonb LANGUAGE sql STABLE AS $$
        SELECT usp_api_success(jsonb_build_object('items',
            (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'category_id', c.category_id, 'category_name', c.category_name,
                'description', c.description) ORDER BY c.category_name), '[]'::jsonb)
            FROM enquiry_categories c WHERE c.is_active)));
    $$;
    ''',
]

# --------------------------------------------------------------------------
# 3. Staff enquiry procedures (list / detail / update / assign / status /
#    remarks / attachments). Every procedure receives the acting user so the
#    same authorisation rules as the web layer are enforced inside the DB.
# --------------------------------------------------------------------------
STAFF_PROCEDURES = [
    # -- Shared row shape used by list/get/update ----------------------------
    r'''
    CREATE OR REPLACE FUNCTION usp_enquiry_row_json(p_enquiry_id integer)
    RETURNS jsonb LANGUAGE sql STABLE AS $$
        SELECT jsonb_build_object(
            'enquiry_id', e.enquiry_id,
            'enquiry_number', e.enquiry_number,
            'full_name', e.full_name,
            'mobile_number', e.mobile_number,
            'email', e.email,
            'state', e.state,
            'district', e.district,
            'category_id', e.category_id,
            'category_name', c.category_name,
            'subject', e.subject,
            'description', e.description,
            'source', e.source,
            'status', e.status,
            'created_by_user_id', e.created_by_user_id,
            'created_by_name', cu.full_name,
            'assigned_to_user_id', e.assigned_to_user_id,
            'assigned_to_name', au.full_name,
            'created_at', e.created_at,
            'updated_at', e.updated_at)
        FROM enquiries e
        LEFT JOIN enquiry_categories c ON c.category_id = e.category_id
        LEFT JOIN users cu ON cu.user_id = e.created_by_user_id
        LEFT JOIN users au ON au.user_id = e.assigned_to_user_id
        WHERE e.enquiry_id = p_enquiry_id;
    $$;
    ''',
    # -- Status workflow metadata (mirrors STATUS_TRANSITIONS) ---------------
    r'''
    CREATE OR REPLACE FUNCTION usp_allowed_status_transitions()
    RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
        SELECT jsonb_build_object(
            'New', jsonb_build_array('Pending', 'Assigned', 'In Progress', 'Resolved', 'Closed', 'Rejected'),
            'Pending', jsonb_build_array('In Progress', 'Resolved', 'Closed', 'Rejected'),
            'Assigned', jsonb_build_array('In Progress', 'Pending', 'Resolved', 'Closed', 'Rejected'),
            'In Progress', jsonb_build_array('Pending', 'Resolved', 'Closed', 'Rejected'),
            'Resolved', jsonb_build_array('Closed', 'Reopened'),
            'Closed', jsonb_build_array('Reopened'),
            'Rejected', jsonb_build_array('Reopened'),
            'Reopened', jsonb_build_array('In Progress', 'Pending', 'Resolved', 'Rejected', 'Closed'));
    $$;
    ''',
    # -- Create an enquiry on behalf of a visitor (staff / employee) ---------
    r'''
    CREATE OR REPLACE FUNCTION usp_create_staff_enquiry(
        p_actor_user_id integer, p_full_name text, p_mobile_number text,
        p_category_id integer, p_subject text, p_description text,
        p_email text DEFAULT NULL, p_state text DEFAULT NULL,
        p_district text DEFAULT NULL, p_source text DEFAULT 'ADMIN',
        p_ip_address text DEFAULT NULL)
    RETURNS jsonb LANGUAGE plpgsql AS $$
    DECLARE
        v_category_id integer;
        v_enquiry_id integer;
        v_enquiry_number text;
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM users WHERE user_id = p_actor_user_id AND is_active) THEN
            RETURN usp_api_error('FORBIDDEN', 'You must be signed in to create enquiries.');
        END IF;
        IF coalesce(p_source, 'ADMIN') NOT IN ('ADMIN', 'USER') THEN
            RETURN usp_api_error('VALIDATION', 'Source must be ADMIN or USER.');
        END IF;

        p_full_name := btrim(coalesce(p_full_name, ''));
        p_subject := btrim(coalesce(p_subject, ''));
        p_description := btrim(coalesce(p_description, ''));
        IF length(p_full_name) < 2 OR length(p_full_name) > 100 THEN
            RETURN usp_api_error('VALIDATION', 'Full name must be between 2 and 100 characters.');
        END IF;
        p_mobile_number := usp_normalize_mobile(p_mobile_number);
        IF p_mobile_number !~ '^\+?[0-9]{10,15}$' THEN
            RETURN usp_api_error('VALIDATION',
                'Mobile number must contain 10 to 15 digits (a leading + is allowed).');
        END IF;
        IF p_email IS NOT NULL AND btrim(p_email) <> '' THEN
            p_email := btrim(p_email);
            IF length(p_email) > 120 OR p_email !~ '^[^@\s]+@[^@\s]+\.[^@\s]{2,}$' THEN
                RETURN usp_api_error('VALIDATION', 'Please enter a valid email address.');
            END IF;
        ELSE
            p_email := NULL;
        END IF;
        p_state := left(NULLIF(btrim(coalesce(p_state, '')), ''), 50);
        p_district := left(NULLIF(btrim(coalesce(p_district, '')), ''), 50);

        IF p_category_id IS NULL THEN
            RETURN usp_api_error('VALIDATION', 'Please select an enquiry category.');
        END IF;
        SELECT category_id INTO v_category_id FROM enquiry_categories
        WHERE category_id = p_category_id AND is_active;
        IF v_category_id IS NULL THEN
            RETURN usp_api_error('VALIDATION', 'Selected enquiry category is not available.');
        END IF;
        IF length(p_subject) < 5 OR length(p_subject) > 150 THEN
            RETURN usp_api_error('VALIDATION', 'Subject must be between 5 and 150 characters.');
        END IF;
        IF length(p_description) < 10 OR length(p_description) > 5000 THEN
            RETURN usp_api_error('VALIDATION', 'Description must be between 10 and 5000 characters.');
        END IF;

        v_enquiry_number := 'CAF-' || to_char(now(), 'YYYY') || '-'
                            || lpad(nextval('enquiry_number_seq')::text, 6, '0');
        INSERT INTO enquiries (enquiry_number, full_name, mobile_number, email, state, district,
                               category_id, subject, description, source,
                               created_by_user_id, status)
        VALUES (v_enquiry_number, p_full_name, p_mobile_number, p_email, p_state, p_district,
                p_category_id, p_subject, p_description, coalesce(p_source, 'ADMIN'),
                p_actor_user_id, 'New')
        RETURNING enquiry_id INTO v_enquiry_id;

        INSERT INTO enquiry_status_history
            (enquiry_id, previous_status, new_status, changed_by_user_id, remarks)
        VALUES (v_enquiry_id, NULL, 'New', p_actor_user_id, 'Enquiry created internally');

        INSERT INTO activity_logs (user_id, action, description, enquiry_id, ip_address)
        VALUES (p_actor_user_id, 'ENQUIRY_CREATED',
                'Enquiry ' || v_enquiry_number || ' created (' || coalesce(p_source, 'ADMIN') || ')',
                v_enquiry_id, p_ip_address);

        RETURN usp_api_success(usp_enquiry_row_json(v_enquiry_id));
    END;
    $$;
    ''',
    # -- Paginated, filtered enquiry list scoped to the viewer ---------------
    r'''
    CREATE OR REPLACE FUNCTION usp_list_enquiries(
        p_viewer_user_id integer, p_is_admin boolean,
        p_search text DEFAULT NULL, p_status text DEFAULT NULL,
        p_category_id integer DEFAULT NULL, p_source text DEFAULT NULL,
        p_assigned_to_user_id integer DEFAULT NULL,
        p_date_from date DEFAULT NULL, p_date_to date DEFAULT NULL,
        p_page integer DEFAULT 1, p_page_size integer DEFAULT 10)
    RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
    DECLARE
        v_search text;
        v_offset integer;
        v_total integer;
        v_pages integer;
        v_items jsonb;
    BEGIN
        v_search := NULLIF(btrim(coalesce(p_search, '')), '');
        p_page := GREATEST(coalesce(p_page, 1), 1);
        p_page_size := LEAST(GREATEST(coalesce(p_page_size, 10), 1), 100);
        v_offset := (p_page - 1) * p_page_size;

        SELECT count(*) INTO v_total
        FROM enquiries e
        WHERE (p_is_admin OR e.created_by_user_id = p_viewer_user_id
               OR e.assigned_to_user_id = p_viewer_user_id)
          AND (v_search IS NULL OR e.enquiry_number ILIKE '%' || v_search || '%'
               OR e.full_name ILIKE '%' || v_search || '%'
               OR e.mobile_number ILIKE '%' || v_search || '%'
               OR e.subject ILIKE '%' || v_search || '%')
          AND (p_status IS NULL OR e.status = p_status)
          AND (p_category_id IS NULL OR e.category_id = p_category_id)
          AND (p_source IS NULL OR e.source = p_source)
          AND (p_assigned_to_user_id IS NULL OR e.assigned_to_user_id = p_assigned_to_user_id)
          AND (p_date_from IS NULL OR e.created_at >= p_date_from)
          AND (p_date_to IS NULL OR e.created_at < p_date_to + interval '1 day');

        SELECT COALESCE(jsonb_agg(x.row), '[]'::jsonb) INTO v_items
        FROM (
            SELECT usp_enquiry_row_json(e.enquiry_id) AS row
            FROM enquiries e
            WHERE (p_is_admin OR e.created_by_user_id = p_viewer_user_id
                   OR e.assigned_to_user_id = p_viewer_user_id)
              AND (v_search IS NULL OR e.enquiry_number ILIKE '%' || v_search || '%'
                   OR e.full_name ILIKE '%' || v_search || '%'
                   OR e.mobile_number ILIKE '%' || v_search || '%'
                   OR e.subject ILIKE '%' || v_search || '%')
              AND (p_status IS NULL OR e.status = p_status)
              AND (p_category_id IS NULL OR e.category_id = p_category_id)
              AND (p_source IS NULL OR e.source = p_source)
              AND (p_assigned_to_user_id IS NULL OR e.assigned_to_user_id = p_assigned_to_user_id)
              AND (p_date_from IS NULL OR e.created_at >= p_date_from)
              AND (p_date_to IS NULL OR e.created_at < p_date_to + interval '1 day')
            ORDER BY e.created_at DESC
            LIMIT p_page_size OFFSET v_offset) x;

        v_pages := (v_total + p_page_size - 1) / p_page_size;
        RETURN usp_api_success(jsonb_build_object(
            'items', v_items, 'total', v_total,
            'page', p_page, 'page_size', p_page_size, 'pages', v_pages));
    END;
    $$;
    ''',
    # -- Full enquiry detail with history / attachments / assignment ---------
    r'''
    CREATE OR REPLACE FUNCTION usp_get_enquiry(
        p_viewer_user_id integer, p_is_admin boolean, p_enquiry_id integer)
    RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
    DECLARE
        v_row jsonb;
        v_history jsonb;
        v_attachments jsonb;
        v_assignment jsonb;
    BEGIN
        SELECT * INTO v_row FROM enquiries WHERE enquiry_id = p_enquiry_id;
        IF NOT FOUND THEN
            RETURN usp_api_error('NOT_FOUND', 'Enquiry not found.');
        END IF;
        IF NOT (p_is_admin OR EXISTS (
                SELECT 1 FROM enquiries e
                WHERE e.enquiry_id = p_enquiry_id
                  AND (e.created_by_user_id = p_viewer_user_id
                       OR e.assigned_to_user_id = p_viewer_user_id))) THEN
            RETURN usp_api_error('NOT_FOUND', 'Enquiry not found.');
        END IF;

        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'history_id', h.history_id, 'previous_status', h.previous_status,
                   'new_status', h.new_status, 'remarks', h.remarks,
                   'changed_by_user_id', h.changed_by_user_id,
                   'changed_by_name', u.full_name, 'changed_at', h.changed_at)
                   ORDER BY h.changed_at, h.history_id), '[]'::jsonb)
          INTO v_history
          FROM enquiry_status_history h
          LEFT JOIN users u ON u.user_id = h.changed_by_user_id
         WHERE h.enquiry_id = p_enquiry_id;

        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'attachment_id', a.attachment_id,
                   'original_file_name', a.original_file_name,
                   'stored_file_name', a.stored_file_name,
                   'file_size', a.file_size, 'content_type', a.content_type,
                   'uploaded_by_user_id', a.uploaded_by_user_id,
                   'uploaded_by_name', u.full_name, 'created_at', a.created_at)
                   ORDER BY a.created_at, a.attachment_id), '[]'::jsonb)
          INTO v_attachments
          FROM enquiry_attachments a
          LEFT JOIN users u ON u.user_id = a.uploaded_by_user_id
         WHERE a.enquiry_id = p_enquiry_id;

        SELECT jsonb_build_object(
                   'assignment_id', s.assignment_id,
                   'assigned_to_user_id', s.assigned_to_user_id,
                   'assigned_to_name', tu.full_name,
                   'assigned_by_user_id', s.assigned_by_user_id,
                   'assigned_by_name', bu.full_name,
                   'remarks', s.remarks, 'assigned_at', s.assigned_at)
          INTO v_assignment
          FROM enquiry_assignments s
          LEFT JOIN users tu ON tu.user_id = s.assigned_to_user_id
          LEFT JOIN users bu ON bu.user_id = s.assigned_by_user_id
         WHERE s.enquiry_id = p_enquiry_id AND s.is_current
         LIMIT 1;

        RETURN usp_api_success(jsonb_build_object(
            'enquiry', usp_enquiry_row_json(p_enquiry_id),
            'history', v_history,
            'attachments', v_attachments,
            'current_assignment', v_assignment));
    END;
    $$;
    ''',
    # -- Update enquiry fields (Admin: any enquiry, creator: own) ------------
    r'''
    CREATE OR REPLACE FUNCTION usp_update_enquiry(
        p_viewer_user_id integer, p_is_admin boolean, p_enquiry_id integer,
        p_full_name text, p_mobile_number text, p_email text DEFAULT NULL,
        p_state text DEFAULT NULL, p_district text DEFAULT NULL,
        p_category_id integer DEFAULT NULL, p_subject text DEFAULT NULL,
        p_description text DEFAULT NULL, p_ip_address text DEFAULT NULL)
    RETURNS jsonb LANGUAGE plpgsql AS $$
    DECLARE
        v_enquiry enquiries%ROWTYPE;
        v_category_id integer;
    BEGIN
        SELECT * INTO v_enquiry FROM enquiries WHERE enquiry_id = p_enquiry_id;
        IF NOT FOUND THEN
            RETURN usp_api_error('NOT_FOUND', 'Enquiry not found.');
        END IF;
        IF NOT (p_is_admin OR v_enquiry.created_by_user_id = p_viewer_user_id) THEN
            RETURN usp_api_error('FORBIDDEN',
                'You are not allowed to edit this enquiry.');
        END IF;
        -- Employees cannot edit enquiries that reached a final status.
        IF NOT p_is_admin AND v_enquiry.status IN ('Resolved', 'Closed', 'Rejected') THEN
            RETURN usp_api_error('FORBIDDEN',
                'This enquiry has reached a final status and can no longer be edited.');
        END IF;

        p_full_name := btrim(coalesce(p_full_name, ''));
        p_subject := btrim(coalesce(p_subject, ''));
        p_description := btrim(coalesce(p_description, ''));
        IF length(p_full_name) < 2 OR length(p_full_name) > 100 THEN
            RETURN usp_api_error('VALIDATION', 'Full name must be between 2 and 100 characters.');
        END IF;
        p_mobile_number := usp_normalize_mobile(p_mobile_number);
        IF p_mobile_number !~ '^\+?[0-9]{10,15}$' THEN
            RETURN usp_api_error('VALIDATION',
                'Mobile number must contain 10 to 15 digits (a leading + is allowed).');
        END IF;
        IF p_email IS NOT NULL AND btrim(p_email) <> '' THEN
            p_email := btrim(p_email);
            IF length(p_email) > 120 OR p_email !~ '^[^@\s]+@[^@\s]+\.[^@\s]{2,}$' THEN
                RETURN usp_api_error('VALIDATION', 'Please enter a valid email address.');
            END IF;
        ELSE
            p_email := NULL;
        END IF;
        p_state := left(NULLIF(btrim(coalesce(p_state, '')), ''), 50);
        p_district := left(NULLIF(btrim(coalesce(p_district, '')), ''), 50);

        IF p_category_id IS NULL THEN
            RETURN usp_api_error('VALIDATION', 'Please select an enquiry category.');
        END IF;
        SELECT category_id INTO v_category_id FROM enquiry_categories
        WHERE category_id = p_category_id;
        IF v_category_id IS NULL THEN
            RETURN usp_api_error('VALIDATION', 'Selected enquiry category does not exist.');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM enquiry_categories
                       WHERE category_id = p_category_id AND is_active)
           AND p_category_id <> v_enquiry.category_id THEN
            RETURN usp_api_error('VALIDATION', 'Selected enquiry category is not active.');
        END IF;
        IF length(p_subject) < 5 OR length(p_subject) > 150 THEN
            RETURN usp_api_error('VALIDATION', 'Subject must be between 5 and 150 characters.');
        END IF;
        IF length(p_description) < 10 OR length(p_description) > 5000 THEN
            RETURN usp_api_error('VALIDATION', 'Description must be between 10 and 5000 characters.');
        END IF;

        UPDATE enquiries SET
            full_name = p_full_name, mobile_number = p_mobile_number, email = p_email,
            state = p_state, district = p_district, category_id = p_category_id,
            subject = p_subject, description = p_description
        WHERE enquiry_id = p_enquiry_id;

        INSERT INTO activity_logs (user_id, action, description, enquiry_id, ip_address)
        VALUES (p_viewer_user_id, 'ENQUIRY_UPDATED',
                'Enquiry ' || v_enquiry.enquiry_number || ' details updated',
                p_enquiry_id, p_ip_address);

        RETURN usp_api_success(usp_enquiry_row_json(p_enquiry_id));
    END;
    $$;
    ''',
    # -- Admin assign / reassign an enquiry to an employee -------------------
    r'''
    CREATE OR REPLACE FUNCTION usp_assign_enquiry(
        p_admin_user_id integer, p_enquiry_id integer, p_assigned_to_user_id integer,
        p_remarks text DEFAULT NULL, p_ip_address text DEFAULT NULL)
    RETURNS jsonb LANGUAGE plpgsql AS $$
    DECLARE
        v_enquiry enquiries%ROWTYPE;
        v_target record;
        v_previous_assignee integer;
        v_remarks text;
        v_action text;
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM users u JOIN roles r ON r.role_id = u.role_id
                       WHERE u.user_id = p_admin_user_id AND r.role_name = 'Admin') THEN
            RETURN usp_api_error('FORBIDDEN', 'Only Admin can assign enquiries.');
        END IF;
        SELECT * INTO v_enquiry FROM enquiries WHERE enquiry_id = p_enquiry_id;
        IF NOT FOUND THEN
            RETURN usp_api_error('NOT_FOUND', 'Enquiry not found.');
        END IF;
        SELECT u.user_id, u.full_name INTO v_target FROM users u
        WHERE u.user_id = p_assigned_to_user_id AND u.is_active;
        IF NOT FOUND THEN
            RETURN usp_api_error('VALIDATION', 'The selected employee does not exist or is inactive.');
        END IF;

        v_remarks := NULLIF(btrim(coalesce(p_remarks, '')), '');

        UPDATE enquiry_assignments SET is_current = false
        WHERE enquiry_id = p_enquiry_id AND is_current;

        INSERT INTO enquiry_assignments
            (enquiry_id, assigned_to_user_id, assigned_by_user_id, remarks, is_current)
        VALUES (p_enquiry_id, v_target.user_id, p_admin_user_id, v_remarks, true);

        v_previous_assignee := v_enquiry.assigned_to_user_id;
        UPDATE enquiries SET assigned_to_user_id = v_target.user_id
        WHERE enquiry_id = p_enquiry_id;

        IF v_enquiry.status IN ('New', 'Pending', 'Reopened') THEN
            UPDATE enquiries SET status = 'Assigned' WHERE enquiry_id = p_enquiry_id;
            INSERT INTO enquiry_status_history
                (enquiry_id, previous_status, new_status, changed_by_user_id, remarks)
            VALUES (p_enquiry_id, v_enquiry.status, 'Assigned', p_admin_user_id,
                    coalesce(v_remarks, 'Assigned to ' || v_target.full_name));
        END IF;

        v_action := CASE
            WHEN v_previous_assignee IS NOT NULL AND v_previous_assignee <> v_target.user_id
                THEN 'ENQUIRY_REASSIGNED'
            ELSE 'ENQUIRY_ASSIGNED' END;
        INSERT INTO activity_logs (user_id, action, description, enquiry_id, ip_address)
        VALUES (p_admin_user_id, v_action,
                'Enquiry ' || v_enquiry.enquiry_number || ' assigned to ' || v_target.full_name,
                p_enquiry_id, p_ip_address);

        RETURN usp_api_success(jsonb_build_object(
            'enquiry_id', p_enquiry_id,
            'enquiry_number', v_enquiry.enquiry_number,
            'assigned_to_user_id', v_target.user_id,
            'assigned_to_name', v_target.full_name,
            'status', CASE WHEN v_enquiry.status IN ('New', 'Pending', 'Reopened')
                           THEN 'Assigned' ELSE v_enquiry.status END));
    END;
    $$;
    ''',
    # -- Change enquiry status with workflow + role enforcement --------------
    r'''
    CREATE OR REPLACE FUNCTION usp_update_enquiry_status(
        p_viewer_user_id integer, p_is_admin boolean, p_enquiry_id integer,
        p_new_status text, p_remarks text DEFAULT NULL, p_ip_address text DEFAULT NULL)
    RETURNS jsonb LANGUAGE plpgsql AS $$
    DECLARE
        v_enquiry enquiries%ROWTYPE;
        v_remarks text;
        v_allowed jsonb;
    BEGIN
        SELECT * INTO v_enquiry FROM enquiries WHERE enquiry_id = p_enquiry_id;
        IF NOT FOUND THEN
            RETURN usp_api_error('NOT_FOUND', 'Enquiry not found.');
        END IF;
        IF NOT (p_is_admin OR v_enquiry.created_by_user_id = p_viewer_user_id
                OR v_enquiry.assigned_to_user_id = p_viewer_user_id) THEN
            RETURN usp_api_error('NOT_FOUND', 'Enquiry not found.');
        END IF;

        -- Employees: terminal states are final, and only the creator may
        -- progress a status (an assignment does not grant that right).
        IF NOT p_is_admin THEN
            IF v_enquiry.status IN ('Resolved', 'Closed', 'Rejected') THEN
                RETURN usp_api_error('FORBIDDEN',
                    'This enquiry has reached a final status and can no longer be updated.');
            END IF;
            IF v_enquiry.created_by_user_id IS DISTINCT FROM p_viewer_user_id THEN
                RETURN usp_api_error('FORBIDDEN',
                    'You can only update the status of enquiries you created.');
            END IF;
        END IF;

        p_new_status := btrim(coalesce(p_new_status, ''));
        IF p_new_status NOT IN ('New', 'Pending', 'Assigned', 'In Progress',
                                'Resolved', 'Closed', 'Rejected', 'Reopened') THEN
            RETURN usp_api_error('VALIDATION', 'Unknown enquiry status.');
        END IF;
        v_allowed := usp_allowed_status_transitions() -> v_enquiry.status;
        IF v_allowed IS NULL THEN
            RETURN usp_api_error('VALIDATION', 'The current status does not allow any transition.');
        END IF;
        IF NOT (v_allowed ? p_new_status) THEN
            RETURN usp_api_error('VALIDATION',
                'Status cannot change from ' || chr(39) || v_enquiry.status || chr(39)
                    || ' to ' || chr(39) || p_new_status || chr(39) || '.');
        END IF;
        IF p_new_status IN ('Closed', 'Rejected', 'Reopened') AND NOT p_is_admin THEN
            RETURN usp_api_error('FORBIDDEN',
                'Only Admin can mark enquiries as Closed, Rejected or Reopened.');
        END IF;

        v_remarks := NULLIF(btrim(coalesce(p_remarks, '')), '');
        IF p_new_status = 'Reopened' AND v_remarks IS NULL THEN
            RETURN usp_api_error('VALIDATION', 'A reason is required when reopening an enquiry.');
        END IF;

        INSERT INTO enquiry_status_history
            (enquiry_id, previous_status, new_status, changed_by_user_id, remarks)
        VALUES (p_enquiry_id, v_enquiry.status, p_new_status, p_viewer_user_id, v_remarks);
        UPDATE enquiries SET status = p_new_status WHERE enquiry_id = p_enquiry_id;

        INSERT INTO activity_logs (user_id, action, description, enquiry_id, ip_address)
        VALUES (p_viewer_user_id,
                CASE WHEN p_new_status = 'Reopened' THEN 'ENQUIRY_REOPENED' ELSE 'STATUS_CHANGED' END,
                'Enquiry ' || v_enquiry.enquiry_number || ': ' || v_enquiry.status
                    || ' -> ' || p_new_status,
                p_enquiry_id, p_ip_address);

        RETURN usp_api_success(jsonb_build_object(
            'enquiry_id', p_enquiry_id,
            'enquiry_number', v_enquiry.enquiry_number,
            'previous_status', v_enquiry.status,
            'new_status', p_new_status));
    END;
    $$;
    ''',
    # -- Add a remark without changing status --------------------------------
    r'''
    CREATE OR REPLACE FUNCTION usp_add_enquiry_remark(
        p_viewer_user_id integer, p_is_admin boolean, p_enquiry_id integer,
        p_remarks text, p_ip_address text DEFAULT NULL)
    RETURNS jsonb LANGUAGE plpgsql AS $$
    DECLARE
        v_enquiry enquiries%ROWTYPE;
        v_remarks text;
    BEGIN
        v_remarks := btrim(coalesce(p_remarks, ''));
        IF v_remarks = '' THEN
            RETURN usp_api_error('VALIDATION', 'Remark text is required.');
        END IF;
        SELECT * INTO v_enquiry FROM enquiries WHERE enquiry_id = p_enquiry_id;
        IF NOT FOUND THEN
            RETURN usp_api_error('NOT_FOUND', 'Enquiry not found.');
        END IF;
        IF NOT (p_is_admin OR v_enquiry.created_by_user_id = p_viewer_user_id
                OR v_enquiry.assigned_to_user_id = p_viewer_user_id) THEN
            RETURN usp_api_error('NOT_FOUND', 'Enquiry not found.');
        END IF;

        INSERT INTO enquiry_status_history
            (enquiry_id, previous_status, new_status, changed_by_user_id, remarks)
        VALUES (p_enquiry_id, v_enquiry.status, v_enquiry.status, p_viewer_user_id, v_remarks);

        INSERT INTO activity_logs (user_id, action, description, enquiry_id, ip_address)
        VALUES (p_viewer_user_id, 'REMARK_ADDED',
                'Remark added on enquiry ' || v_enquiry.enquiry_number,
                p_enquiry_id, p_ip_address);

        RETURN usp_api_success(jsonb_build_object(
            'enquiry_id', p_enquiry_id,
            'enquiry_number', v_enquiry.enquiry_number,
            'status', v_enquiry.status,
            'remarks_added', true));
    END;
    $$;
    ''',
    # -- Register an uploaded attachment (file saved by Flask) ----------------
    r'''
    CREATE OR REPLACE FUNCTION usp_add_enquiry_attachment(
        p_viewer_user_id integer, p_is_admin boolean, p_enquiry_id integer,
        p_original_file_name text, p_stored_file_name text, p_file_path text,
        p_file_size bigint, p_content_type text DEFAULT 'application/octet-stream',
        p_ip_address text DEFAULT NULL,
        p_allowed_extensions text[] DEFAULT ARRAY['pdf', 'png', 'jpg', 'jpeg', 'gif',
                                                  'doc', 'docx', 'xls', 'xlsx', 'csv', 'txt'],
        p_max_size_mb integer DEFAULT 5)
    RETURNS jsonb LANGUAGE plpgsql AS $$
    DECLARE
        v_enquiry enquiries%ROWTYPE;
        v_extension text;
        v_attachment_id integer;
    BEGIN
        SELECT * INTO v_enquiry FROM enquiries WHERE enquiry_id = p_enquiry_id;
        IF NOT FOUND THEN
            RETURN usp_api_error('NOT_FOUND', 'Enquiry not found.');
        END IF;
        IF NOT (p_is_admin OR v_enquiry.created_by_user_id = p_viewer_user_id
                OR v_enquiry.assigned_to_user_id = p_viewer_user_id) THEN
            RETURN usp_api_error('NOT_FOUND', 'Enquiry not found.');
        END IF;

        IF p_original_file_name IS NULL OR btrim(p_original_file_name) = '' THEN
            RETURN usp_api_error('VALIDATION', 'No file selected.');
        END IF;
        v_extension := lower(reverse(split_part(reverse(p_original_file_name), '.', 1)));
        IF v_extension = p_original_file_name OR NOT (v_extension = ANY (p_allowed_extensions)) THEN
            RETURN usp_api_error('VALIDATION', 'This file type is not allowed.');
        END IF;
        IF p_file_size > p_max_size_mb * 1024 * 1024 THEN
            RETURN usp_api_error('VALIDATION',
                'File exceeds the ' || p_max_size_mb || ' MB limit.');
        END IF;
        IF p_file_size <= 0 THEN
            RETURN usp_api_error('VALIDATION', 'The uploaded file is empty.');
        END IF;

        INSERT INTO enquiry_attachments
            (enquiry_id, original_file_name, stored_file_name, file_path,
             file_size, content_type, uploaded_by_user_id)
        VALUES (p_enquiry_id, p_original_file_name, p_stored_file_name, p_file_path,
                p_file_size, coalesce(p_content_type, 'application/octet-stream'),
                p_viewer_user_id)
        RETURNING attachment_id INTO v_attachment_id;

        INSERT INTO activity_logs (user_id, action, description, enquiry_id, ip_address)
        VALUES (p_viewer_user_id, 'ATTACHMENT_UPLOADED',
                'Attachment ' || chr(39) || p_original_file_name || chr(39) || ' added to '
                    || v_enquiry.enquiry_number,
                p_enquiry_id, p_ip_address);

        RETURN usp_api_success(jsonb_build_object(
            'attachment_id', v_attachment_id,
            'enquiry_number', v_enquiry.enquiry_number,
            'original_file_name', p_original_file_name,
            'stored_file_name', p_stored_file_name,
            'file_size', p_file_size));
    END;
    $$;
    ''',
    # -- Delete an attachment; returns the path so Flask can unlink it -------
    r'''
    CREATE OR REPLACE FUNCTION usp_delete_enquiry_attachment(
        p_viewer_user_id integer, p_is_admin boolean, p_attachment_id integer,
        p_ip_address text DEFAULT NULL)
    RETURNS jsonb LANGUAGE plpgsql AS $$
    DECLARE
        v_attachment enquiry_attachments%ROWTYPE;
        v_enquiry_number text;
    BEGIN
        SELECT * INTO v_attachment
        FROM enquiry_attachments a
        WHERE a.attachment_id = p_attachment_id;
        IF NOT FOUND THEN
            RETURN usp_api_error('NOT_FOUND', 'Attachment not found.');
        END IF;
        SELECT e.enquiry_number INTO v_enquiry_number
        FROM enquiries e WHERE e.enquiry_id = v_attachment.enquiry_id;
        IF NOT (p_is_admin OR v_attachment.uploaded_by_user_id = p_viewer_user_id) THEN
            RETURN usp_api_error('FORBIDDEN', 'You are not allowed to delete this attachment.');
        END IF;

        DELETE FROM enquiry_attachments WHERE attachment_id = p_attachment_id;

        INSERT INTO activity_logs (user_id, action, description, enquiry_id, ip_address)
        VALUES (p_viewer_user_id, 'ATTACHMENT_DELETED',
                'Attachment ' || chr(39) || v_attachment.original_file_name || chr(39) || ' removed from '
                    || v_enquiry_number,
                v_attachment.enquiry_id, p_ip_address);

        RETURN usp_api_success(jsonb_build_object(
            'attachment_id', p_attachment_id,
            'enquiry_number', v_enquiry_number,
            'file_path', v_attachment.file_path));
    END;
    $$;
    ''',
]

# --------------------------------------------------------------------------
# 4. Dashboard procedures — KPI cards and chart datasets (Admin: everything,
#    employees: their own created-or-assigned scope). The chart JSON shapes
#    mirror dashboard.js exactly.
# --------------------------------------------------------------------------
DASHBOARD_PROCEDURES = [
    r'''
    CREATE OR REPLACE FUNCTION usp_get_dashboard_kpis(
        p_viewer_user_id integer, p_is_admin boolean)
    RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
    DECLARE
        v_kpis jsonb;
    BEGIN
        IF p_is_admin THEN
            SELECT jsonb_build_array(
                jsonb_build_object('label', 'Total Enquiries', 'value',
                                   (SELECT count(*) FROM enquiries), 'status', NULL::text),
                jsonb_build_object('label', 'New Enquiries', 'value',
                                   (SELECT count(*) FROM enquiries WHERE status = 'New'),
                                   'status', 'New'),
                jsonb_build_object('label', 'Pending Enquiries', 'value',
                                   (SELECT count(*) FROM enquiries WHERE status = 'Pending'),
                                   'status', 'Pending'),
                jsonb_build_object('label', 'Assigned Enquiries', 'value',
                                   (SELECT count(*) FROM enquiries WHERE status = 'Assigned'),
                                   'status', 'Assigned'),
                jsonb_build_object('label', 'In Progress Enquiries', 'value',
                                   (SELECT count(*) FROM enquiries WHERE status = 'In Progress'),
                                   'status', 'In Progress'),
                jsonb_build_object('label', 'Resolved Enquiries', 'value',
                                   (SELECT count(*) FROM enquiries WHERE status = 'Resolved'),
                                   'status', 'Resolved'),
                jsonb_build_object('label', 'Closed Enquiries', 'value',
                                   (SELECT count(*) FROM enquiries WHERE status = 'Closed'),
                                   'status', 'Closed'),
                jsonb_build_object('label', 'Rejected Enquiries', 'value',
                                   (SELECT count(*) FROM enquiries WHERE status = 'Rejected'),
                                   'status', 'Rejected'))
              INTO v_kpis;
        ELSE
            SELECT jsonb_build_array(
                jsonb_build_object('label', 'Enquiries Created by Me', 'value',
                    (SELECT count(*) FROM enquiries WHERE created_by_user_id = p_viewer_user_id),
                    'status', NULL::text),
                jsonb_build_object('label', 'Enquiries Assigned to Me', 'value',
                    (SELECT count(*) FROM enquiries WHERE assigned_to_user_id = p_viewer_user_id),
                    'status', NULL::text),
                jsonb_build_object('label', 'New Enquiries', 'value',
                    (SELECT count(*) FROM enquiries
                     WHERE status = 'New' AND (created_by_user_id = p_viewer_user_id
                                               OR assigned_to_user_id = p_viewer_user_id)),
                    'status', 'New'),
                jsonb_build_object('label', 'Pending Enquiries', 'value',
                    (SELECT count(*) FROM enquiries
                     WHERE status = 'Pending' AND (created_by_user_id = p_viewer_user_id
                                                   OR assigned_to_user_id = p_viewer_user_id)),
                    'status', 'Pending'),
                jsonb_build_object('label', 'In Progress Enquiries', 'value',
                    (SELECT count(*) FROM enquiries
                     WHERE status = 'In Progress' AND (created_by_user_id = p_viewer_user_id
                                                       OR assigned_to_user_id = p_viewer_user_id)),
                    'status', 'In Progress'),
                jsonb_build_object('label', 'Resolved Enquiries', 'value',
                    (SELECT count(*) FROM enquiries
                     WHERE status = 'Resolved' AND (created_by_user_id = p_viewer_user_id
                                                    OR assigned_to_user_id = p_viewer_user_id)),
                    'status', 'Resolved'))
              INTO v_kpis;
        END IF;
        RETURN usp_api_success(jsonb_build_object('kpis', v_kpis));
    END;
    $$;
    ''',
    r'''
    CREATE OR REPLACE FUNCTION usp_get_dashboard_charts(
        p_viewer_user_id integer, p_is_admin boolean)
    RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
    DECLARE
        v_status jsonb;
        v_category jsonb;
        v_source jsonb;
        v_trend jsonb;
        v_employee_assigned jsonb;
        v_employee_resolved jsonb;
    BEGIN
        -- status distribution: {"labels": [...], "counts": [...]}
        SELECT jsonb_build_object(
                   'labels', COALESCE(jsonb_agg(g.status ORDER BY g.status), '[]'::jsonb),
                   'counts', COALESCE(jsonb_agg(g.cnt ORDER BY g.status), '[]'::jsonb))
          INTO v_status
        FROM (SELECT e.status, count(*) AS cnt
              FROM enquiries e
              WHERE p_is_admin OR e.created_by_user_id = p_viewer_user_id
                    OR e.assigned_to_user_id = p_viewer_user_id
              GROUP BY e.status) g;

        -- by category: [labels, counts] ordered by count desc
        SELECT jsonb_build_array(
                   COALESCE(jsonb_agg(g.category_name ORDER BY g.cnt DESC), '[]'::jsonb),
                   COALESCE(jsonb_agg(g.cnt ORDER BY g.cnt DESC), '[]'::jsonb))
          INTO v_category
        FROM (SELECT c.category_name, count(*) AS cnt
              FROM enquiries e
              JOIN enquiry_categories c ON c.category_id = e.category_id
              WHERE p_is_admin OR e.created_by_user_id = p_viewer_user_id
                    OR e.assigned_to_user_id = p_viewer_user_id
              GROUP BY c.category_name) g;

        -- by source: [labels, counts] (labels title-cased, count desc)
        SELECT jsonb_build_array(
                   COALESCE(jsonb_agg(initcap(g.source) ORDER BY g.cnt DESC), '[]'::jsonb),
                   COALESCE(jsonb_agg(g.cnt ORDER BY g.cnt DESC), '[]'::jsonb))
          INTO v_source
        FROM (SELECT e.source, count(*) AS cnt
              FROM enquiries e
              WHERE p_is_admin OR e.created_by_user_id = p_viewer_user_id
                    OR e.assigned_to_user_id = p_viewer_user_id
              GROUP BY e.source) g;

        -- monthly trend: always six consecutive months including zeros
        SELECT jsonb_build_object(
                   'labels', COALESCE(jsonb_agg(t.label ORDER BY t.month_start), '[]'::jsonb),
                   'counts', COALESCE(jsonb_agg(t.cnt ORDER BY t.month_start), '[]'::jsonb))
          INTO v_trend
        FROM (
            SELECT m AS month_start, to_char(m, 'Mon YYYY') AS label, count(e.enquiry_id) AS cnt
            FROM generate_series(date_trunc('month', now()) - interval '5 months',
                                 date_trunc('month', now()), interval '1 month') m
            LEFT JOIN enquiries e
                   ON date_trunc('month', e.created_at) = m
                  AND (p_is_admin OR e.created_by_user_id = p_viewer_user_id
                       OR e.assigned_to_user_id = p_viewer_user_id)
            GROUP BY m) t;

        -- per-employee load and resolution counts
        SELECT jsonb_build_object(
                   'labels', COALESCE(jsonb_agg(g.full_name ORDER BY g.cnt DESC), '[]'::jsonb),
                   'counts', COALESCE(jsonb_agg(g.cnt ORDER BY g.cnt DESC), '[]'::jsonb))
          INTO v_employee_assigned
        FROM (SELECT u.full_name, count(*) AS cnt
              FROM enquiries e
              JOIN users u ON u.user_id = e.assigned_to_user_id
              WHERE p_is_admin OR e.created_by_user_id = p_viewer_user_id
                    OR e.assigned_to_user_id = p_viewer_user_id
              GROUP BY u.full_name) g;

        SELECT jsonb_build_object(
                   'labels', COALESCE(jsonb_agg(g.full_name ORDER BY g.cnt DESC), '[]'::jsonb),
                   'counts', COALESCE(jsonb_agg(g.cnt ORDER BY g.cnt DESC), '[]'::jsonb))
          INTO v_employee_resolved
        FROM (SELECT u.full_name, count(*) AS cnt
              FROM enquiries e
              JOIN users u ON u.user_id = e.assigned_to_user_id
              WHERE e.status = 'Resolved'
                AND (p_is_admin OR e.created_by_user_id = p_viewer_user_id
                     OR e.assigned_to_user_id = p_viewer_user_id)
              GROUP BY u.full_name) g;

        RETURN usp_api_success(jsonb_build_object(
            'status', v_status,
            'category', v_category,
            'source', v_source,
            'monthly_trend', v_trend,
            'employee_assigned', v_employee_assigned,
            'employee_resolved', v_employee_resolved));
    END;
    $$;
    ''',
]

# --------------------------------------------------------------------------
# 5. Admin procedures — users, categories, activity logs.
# --------------------------------------------------------------------------
ADMIN_PROCEDURES = [
    r'''
    CREATE OR REPLACE FUNCTION usp_list_users(p_search text DEFAULT NULL)
    RETURNS jsonb LANGUAGE sql STABLE AS $$
        WITH matched AS (
            SELECT u.*, r.role_name
            FROM users u JOIN roles r ON r.role_id = u.role_id
            WHERE p_search IS NULL
               OR u.username ILIKE '%' || p_search || '%'
               OR u.full_name ILIKE '%' || p_search || '%'
               OR u.email ILIKE '%' || p_search || '%')
        SELECT usp_api_success(jsonb_build_object('items',
            (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'user_id', x.user_id, 'username', x.username,
                'full_name', x.full_name, 'email', x.email,
                'role_id', x.role_id, 'role_name', x.role_name,
                'is_active', x.is_active, 'created_at', x.created_at)
                ORDER BY x.username), '[]'::jsonb)
            FROM matched x)));
    $$;
    ''',
    # -- Create an internal user; the password hash is computed by Flask -----
    r'''
    CREATE OR REPLACE FUNCTION usp_create_user(
        p_username text, p_full_name text, p_email text, p_password_hash text,
        p_role_id integer, p_is_active boolean DEFAULT true,
        p_actor_user_id integer DEFAULT NULL, p_ip_address text DEFAULT NULL)
    RETURNS jsonb LANGUAGE plpgsql AS $$
    DECLARE
        v_user_id integer;
        v_role_name text;
    BEGIN
        p_username := btrim(coalesce(p_username, ''));
        p_full_name := btrim(coalesce(p_full_name, ''));
        p_email := btrim(coalesce(p_email, ''));

        IF p_username !~ '^[A-Za-z0-9_.-]{3,50}$' THEN
            RETURN usp_api_error('VALIDATION',
                'Username must be 3-50 characters (letters, numbers, dot, dash, underscore).');
        END IF;
        IF length(p_full_name) < 2 OR length(p_full_name) > 100 THEN
            RETURN usp_api_error('VALIDATION', 'Full name must be between 2 and 100 characters.');
        END IF;
        IF p_email = '' OR length(p_email) > 120
           OR p_email !~ '^[^@\s]+@[^@\s]+\.[^@\s]{2,}$' THEN
            RETURN usp_api_error('VALIDATION', 'Please enter a valid email address.');
        END IF;
        IF coalesce(p_password_hash, '') = '' THEN
            RETURN usp_api_error('VALIDATION', 'Password must be at least 8 characters long.');
        END IF;
        SELECT role_name INTO v_role_name FROM roles WHERE role_id = p_role_id;
        IF v_role_name IS NULL THEN
            RETURN usp_api_error('VALIDATION', 'Please select a role.');
        END IF;
        IF EXISTS (SELECT 1 FROM users WHERE username = p_username OR email = p_email) THEN
            RETURN usp_api_error('CONFLICT', 'Username or email already exists.');
        END IF;

        INSERT INTO users (username, full_name, email, password_hash, role_id, is_active)
        VALUES (p_username, p_full_name, p_email, p_password_hash, p_role_id,
                coalesce(p_is_active, true))
        RETURNING user_id INTO v_user_id;

        INSERT INTO activity_logs (user_id, action, description, ip_address)
        VALUES (p_actor_user_id, 'USER_CREATED',
                'User ' || chr(39) || p_username || chr(39) || ' created with role ' || v_role_name, p_ip_address);

        RETURN usp_api_success(jsonb_build_object(
            'user_id', v_user_id, 'username', p_username, 'full_name', p_full_name,
            'email', p_email, 'role_id', p_role_id, 'role_name', v_role_name,
            'is_active', coalesce(p_is_active, true)));
    END;
    $$;
    ''',
    # -- Activate / deactivate a user (Admin only, not on self) --------------
    r'''
    CREATE OR REPLACE FUNCTION usp_toggle_user_status(
        p_actor_user_id integer, p_target_user_id integer, p_ip_address text DEFAULT NULL)
    RETURNS jsonb LANGUAGE plpgsql AS $$
    DECLARE
        v_target record;
        v_active boolean;
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM users u JOIN roles r ON r.role_id = u.role_id
                       WHERE u.user_id = p_actor_user_id AND r.role_name = 'Admin') THEN
            RETURN usp_api_error('FORBIDDEN', 'Only Admin can change user status.');
        END IF;
        SELECT u.user_id, u.username, u.is_active INTO v_target
        FROM users u WHERE u.user_id = p_target_user_id;
        IF NOT FOUND THEN
            RETURN usp_api_error('NOT_FOUND', 'User not found.');
        END IF;
        IF v_target.user_id = p_actor_user_id THEN
            RETURN usp_api_error('CONFLICT', 'You cannot deactivate your own account.');
        END IF;

        v_active := NOT v_target.is_active;
        UPDATE users SET is_active = v_active WHERE user_id = p_target_user_id;

        INSERT INTO activity_logs (user_id, action, description, ip_address)
        VALUES (p_actor_user_id,
                CASE WHEN v_active THEN 'USER_ACTIVATED' ELSE 'USER_DEACTIVATED' END,
                'User ' || chr(39) || v_target.username || chr(39) || ' '
                    || (CASE WHEN v_active THEN 'activated' ELSE 'deactivated' END),
                p_ip_address);

        RETURN usp_api_success(jsonb_build_object(
            'user_id', v_target.user_id, 'username', v_target.username,
            'is_active', v_active));
    END;
    $$;
    ''',
    r'''
    CREATE OR REPLACE FUNCTION usp_list_categories(p_search text DEFAULT NULL)
    RETURNS jsonb LANGUAGE sql STABLE AS $$
        WITH matched AS (
            SELECT c.category_id, c.category_name, c.description, c.is_active,
                   (SELECT count(*) FROM enquiries e WHERE e.category_id = c.category_id)
                       AS enquiry_count
            FROM enquiry_categories c
            WHERE p_search IS NULL
               OR c.category_name ILIKE '%' || p_search || '%'
               OR c.description ILIKE '%' || p_search || '%')
        SELECT usp_api_success(jsonb_build_object('items',
            (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'category_id', x.category_id, 'category_name', x.category_name,
                'description', x.description, 'is_active', x.is_active,
                'enquiry_count', x.enquiry_count) ORDER BY x.category_name), '[]'::jsonb)
            FROM matched x)));
    $$;
    ''',
    r'''
    CREATE OR REPLACE FUNCTION usp_create_category(
        p_category_name text, p_description text DEFAULT NULL,
        p_actor_user_id integer DEFAULT NULL, p_ip_address text DEFAULT NULL)
    RETURNS jsonb LANGUAGE plpgsql AS $$
    DECLARE
        v_category_id integer;
    BEGIN
        p_category_name := btrim(coalesce(p_category_name, ''));
        p_description := NULLIF(btrim(coalesce(p_description, '')), '');
        IF length(p_category_name) < 3 OR length(p_category_name) > 100 THEN
            RETURN usp_api_error('VALIDATION', 'Category name must be between 3 and 100 characters.');
        END IF;
        IF EXISTS (SELECT 1 FROM enquiry_categories WHERE category_name = p_category_name) THEN
            RETURN usp_api_error('CONFLICT', 'A category with this name already exists.');
        END IF;

        INSERT INTO enquiry_categories (category_name, description, is_active)
        VALUES (p_category_name, p_description, true)
        RETURNING category_id INTO v_category_id;

        INSERT INTO activity_logs (user_id, action, description, ip_address)
        VALUES (p_actor_user_id, 'CATEGORY_CREATED',
                'Category ' || chr(39) || p_category_name || chr(39) || ' created', p_ip_address);

        RETURN usp_api_success(jsonb_build_object(
            'category_id', v_category_id, 'category_name', p_category_name,
            'description', p_description, 'is_active', true));
    END;
    $$;
    ''',
    r'''
    CREATE OR REPLACE FUNCTION usp_toggle_category_status(
        p_actor_user_id integer, p_category_id integer, p_ip_address text DEFAULT NULL)
    RETURNS jsonb LANGUAGE plpgsql AS $$
    DECLARE
        v_category record;
        v_active boolean;
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM users u JOIN roles r ON r.role_id = u.role_id
                       WHERE u.user_id = p_actor_user_id AND r.role_name = 'Admin') THEN
            RETURN usp_api_error('FORBIDDEN', 'Only Admin can change category status.');
        END IF;
        SELECT * INTO v_category FROM enquiry_categories WHERE category_id = p_category_id;
        IF NOT FOUND THEN
            RETURN usp_api_error('NOT_FOUND', 'Category not found.');
        END IF;

        v_active := NOT v_category.is_active;
        UPDATE enquiry_categories SET is_active = v_active WHERE category_id = p_category_id;

        INSERT INTO activity_logs (user_id, action, description, ip_address)
        VALUES (p_actor_user_id, 'CATEGORY_UPDATED',
                'Category ' || chr(39) || v_category.category_name || chr(39) || ' '
                    || (CASE WHEN v_active THEN 'activated' ELSE 'deactivated' END),
                p_ip_address);

        RETURN usp_api_success(jsonb_build_object(
            'category_id', v_category.category_id,
            'category_name', v_category.category_name,
            'is_active', v_active));
    END;
    $$;
    ''',
]

# --------------------------------------------------------------------------
# 6. Report procedures — one procedure per report type, all sharing the same
#    filter signature: (p_status, p_category_id, p_date_from, p_date_to,
#    p_source, p_assigned_to_user_id). Dates are plain dates; the procedures
#    make date_to inclusive themselves (+1 day).
# --------------------------------------------------------------------------
REPORT_PROCEDURES = [
    # -- Shared filtered row source (date_to made inclusive internally) ------
    r'''
    CREATE OR REPLACE FUNCTION usp_report_filtered_enquiries(
        p_status text DEFAULT NULL, p_category_id integer DEFAULT NULL,
        p_date_from date DEFAULT NULL, p_date_to date DEFAULT NULL,
        p_source text DEFAULT NULL, p_assigned_to_user_id integer DEFAULT NULL,
        p_ignore_status boolean DEFAULT false)
    RETURNS SETOF enquiries LANGUAGE sql STABLE AS $$
        SELECT e.* FROM enquiries e
        WHERE (p_ignore_status OR p_status IS NULL OR e.status = p_status)
          AND (p_category_id IS NULL OR e.category_id = p_category_id)
          AND (p_date_from IS NULL OR e.created_at >= p_date_from)
          AND (p_date_to IS NULL OR e.created_at < p_date_to + interval '1 day')
          AND (p_source IS NULL OR e.source = p_source)
          AND (p_assigned_to_user_id IS NULL OR e.assigned_to_user_id = p_assigned_to_user_id)
        ORDER BY e.created_at DESC;
    $$;
    ''',
    # -- Detail row (10 columns, mirrors the web "total" report) -------------
    r'''
    CREATE OR REPLACE FUNCTION usp_report_detail_row(p_enquiry_id integer)
    RETURNS jsonb LANGUAGE sql STABLE AS $$
        SELECT jsonb_build_array(
            e.enquiry_number,
            to_char(e.created_at, 'YYYY-MM-DD'),
            e.full_name, e.mobile_number,
            c.category_name, e.subject, initcap(e.source), e.status,
            coalesce(cu.full_name, 'Visitor'),
            coalesce(au.full_name, 'Unassigned'))
        FROM enquiries e
        LEFT JOIN enquiry_categories c ON c.category_id = e.category_id
        LEFT JOIN users cu ON cu.user_id = e.created_by_user_id
        LEFT JOIN users au ON au.user_id = e.assigned_to_user_id
        WHERE e.enquiry_id = p_enquiry_id;
    $$;
    ''',
    # -- Brief row (8 columns, mirrors pending/resolved reports) -------------
    r'''
    CREATE OR REPLACE FUNCTION usp_report_brief_row(p_enquiry_id integer)
    RETURNS jsonb LANGUAGE sql STABLE AS $$
        SELECT jsonb_build_array(
            e.enquiry_number,
            to_char(e.created_at, 'YYYY-MM-DD'),
            e.full_name, e.mobile_number,
            c.category_name, e.subject, e.status,
            coalesce(au.full_name, 'Unassigned'))
        FROM enquiries e
        LEFT JOIN enquiry_categories c ON c.category_id = e.category_id
        LEFT JOIN users au ON au.user_id = e.assigned_to_user_id
        WHERE e.enquiry_id = p_enquiry_id;
    $$;
    ''',
    r'''
    CREATE OR REPLACE FUNCTION usp_report_total_enquiries(
        p_status text DEFAULT NULL, p_category_id integer DEFAULT NULL,
        p_date_from date DEFAULT NULL, p_date_to date DEFAULT NULL,
        p_source text DEFAULT NULL, p_assigned_to_user_id integer DEFAULT NULL)
    RETURNS jsonb LANGUAGE sql STABLE AS $$
        SELECT usp_api_success(jsonb_build_object(
            'title', 'Total Enquiry Report',
            'headers', jsonb_build_array('Enquiry Number', 'Date', 'Full Name', 'Mobile',
                                         'Category', 'Subject', 'Source', 'Status',
                                         'Created By', 'Assigned To'),
            'rows', (SELECT COALESCE(jsonb_agg(usp_report_detail_row(e.enquiry_id)
                                               ORDER BY e.created_at DESC), '[]'::jsonb)
                     FROM usp_report_filtered_enquiries(
                         p_status, p_category_id, p_date_from, p_date_to,
                         p_source, p_assigned_to_user_id) e),
            'total_records', (SELECT count(*) FROM usp_report_filtered_enquiries(
                         p_status, p_category_id, p_date_from, p_date_to,
                         p_source, p_assigned_to_user_id) e)));
    $$;
    ''',
    r'''
    CREATE OR REPLACE FUNCTION usp_report_date_wise(
        p_status text DEFAULT NULL, p_category_id integer DEFAULT NULL,
        p_date_from date DEFAULT NULL, p_date_to date DEFAULT NULL,
        p_source text DEFAULT NULL, p_assigned_to_user_id integer DEFAULT NULL)
    RETURNS jsonb LANGUAGE sql STABLE AS $$
        WITH grouped AS (
            SELECT e.created_at::date AS report_date, count(*) AS cnt
            FROM usp_report_filtered_enquiries(
                NULL, p_category_id, p_date_from, p_date_to,
                p_source, p_assigned_to_user_id, true) e
            GROUP BY 1)
        SELECT usp_api_success(jsonb_build_object(
            'title', 'Date-wise Enquiry Report',
            'headers', jsonb_build_array('Date', 'Total Enquiries'),
            'rows', COALESCE(jsonb_agg(
                jsonb_build_array(to_char(g.report_date, 'YYYY-MM-DD'), g.cnt)
                ORDER BY g.cnt DESC, g.report_date DESC), '[]'::jsonb),
            'total_records', COALESCE((SELECT sum(cnt) FROM grouped), 0)))
        FROM grouped g;
    $$;
    ''',
    r'''
    CREATE OR REPLACE FUNCTION usp_report_status_wise(
        p_status text DEFAULT NULL, p_category_id integer DEFAULT NULL,
        p_date_from date DEFAULT NULL, p_date_to date DEFAULT NULL,
        p_source text DEFAULT NULL, p_assigned_to_user_id integer DEFAULT NULL)
    RETURNS jsonb LANGUAGE sql STABLE AS $$
        WITH grouped AS (
            SELECT e.status, count(*) AS cnt
            FROM usp_report_filtered_enquiries(
                NULL, p_category_id, p_date_from, p_date_to,
                p_source, p_assigned_to_user_id, true) e
            GROUP BY 1)
        SELECT usp_api_success(jsonb_build_object(
            'title', 'Status-wise Enquiry Report',
            'headers', jsonb_build_array('Status', 'Total Enquiries'),
            'rows', COALESCE(jsonb_agg(jsonb_build_array(g.status, g.cnt)
                                       ORDER BY g.cnt DESC, g.status), '[]'::jsonb),
            'total_records', COALESCE((SELECT sum(cnt) FROM grouped), 0)))
        FROM grouped g;
    $$;
    ''',
    r'''
    CREATE OR REPLACE FUNCTION usp_report_category_wise(
        p_status text DEFAULT NULL, p_category_id integer DEFAULT NULL,
        p_date_from date DEFAULT NULL, p_date_to date DEFAULT NULL,
        p_source text DEFAULT NULL, p_assigned_to_user_id integer DEFAULT NULL)
    RETURNS jsonb LANGUAGE sql STABLE AS $$
        WITH grouped AS (
            SELECT coalesce(c.category_name, 'N/A') AS category, count(*) AS cnt
            FROM usp_report_filtered_enquiries(
                NULL, p_category_id, p_date_from, p_date_to,
                p_source, p_assigned_to_user_id, true) e
            LEFT JOIN enquiry_categories c ON c.category_id = e.category_id
            GROUP BY 1)
        SELECT usp_api_success(jsonb_build_object(
            'title', 'Category-wise Enquiry Report',
            'headers', jsonb_build_array('Category', 'Total Enquiries'),
            'rows', COALESCE(jsonb_agg(jsonb_build_array(g.category, g.cnt)
                                       ORDER BY g.cnt DESC, g.category), '[]'::jsonb),
            'total_records', COALESCE((SELECT sum(cnt) FROM grouped), 0)))
        FROM grouped g;
    $$;
    ''',
    r'''
    CREATE OR REPLACE FUNCTION usp_report_employee_wise(
        p_status text DEFAULT NULL, p_category_id integer DEFAULT NULL,
        p_date_from date DEFAULT NULL, p_date_to date DEFAULT NULL,
        p_source text DEFAULT NULL, p_assigned_to_user_id integer DEFAULT NULL)
    RETURNS jsonb LANGUAGE sql STABLE AS $$
        WITH f AS (
            SELECT * FROM usp_report_filtered_enquiries(
                p_status, p_category_id, p_date_from, p_date_to,
                p_source, p_assigned_to_user_id) e),
        assigned_counts AS (
            SELECT e.assigned_to_user_id AS uid, count(*) AS cnt
            FROM f e WHERE e.assigned_to_user_id IS NOT NULL GROUP BY 1),
        created_counts AS (
            SELECT e.created_by_user_id AS uid, count(*) AS cnt
            FROM f e WHERE e.created_by_user_id IS NOT NULL GROUP BY 1),
        ids AS (
            SELECT uid FROM assigned_counts UNION SELECT uid FROM created_counts)
        SELECT usp_api_success(jsonb_build_object(
            'title', 'Employee-wise Enquiry Report',
            'headers', jsonb_build_array('Employee', 'Created', 'Assigned', 'Total'),
            'rows', COALESCE(jsonb_agg(
                jsonb_build_array(u.full_name,
                                  coalesce(cr.cnt, 0), coalesce(asg.cnt, 0),
                                  coalesce(cr.cnt, 0) + coalesce(asg.cnt, 0))
                ORDER BY coalesce(cr.cnt, 0) + coalesce(asg.cnt, 0) DESC, u.full_name),
                '[]'::jsonb),
            'total_records', (SELECT count(*) FROM ids)))
        FROM ids
        JOIN users u ON u.user_id = ids.uid
        LEFT JOIN assigned_counts asg ON asg.uid = ids.uid
        LEFT JOIN created_counts cr ON cr.uid = ids.uid;
    $$;
    ''',
    r'''
    CREATE OR REPLACE FUNCTION usp_report_source_wise(
        p_status text DEFAULT NULL, p_category_id integer DEFAULT NULL,
        p_date_from date DEFAULT NULL, p_date_to date DEFAULT NULL,
        p_source text DEFAULT NULL, p_assigned_to_user_id integer DEFAULT NULL)
    RETURNS jsonb LANGUAGE sql STABLE AS $$
        WITH grouped AS (
            SELECT e.source, count(*) AS cnt
            FROM usp_report_filtered_enquiries(
                NULL, p_category_id, p_date_from, p_date_to,
                p_source, p_assigned_to_user_id, true) e
            GROUP BY 1)
        SELECT usp_api_success(jsonb_build_object(
            'title', 'Source-wise Enquiry Report',
            'headers', jsonb_build_array('Source', 'Total Enquiries'),
            'rows', COALESCE(jsonb_agg(jsonb_build_array(initcap(g.source), g.cnt)
                                       ORDER BY g.cnt DESC, g.source), '[]'::jsonb),
            'total_records', COALESCE((SELECT sum(cnt) FROM grouped), 0)))
        FROM grouped g;
    $$;
    ''',
    r'''
    CREATE OR REPLACE FUNCTION usp_report_pending_enquiries(
        p_status text DEFAULT NULL, p_category_id integer DEFAULT NULL,
        p_date_from date DEFAULT NULL, p_date_to date DEFAULT NULL,
        p_source text DEFAULT NULL, p_assigned_to_user_id integer DEFAULT NULL)
    RETURNS jsonb LANGUAGE sql STABLE AS $$
        SELECT usp_api_success(jsonb_build_object(
            'title', 'Pending Enquiry Report',
            'headers', jsonb_build_array('Enquiry Number', 'Date', 'Full Name', 'Mobile',
                                         'Category', 'Subject', 'Status', 'Assigned To'),
            'rows', COALESCE(jsonb_agg(usp_report_brief_row(e.enquiry_id)
                                       ORDER BY e.created_at), '[]'::jsonb),
            'total_records', count(e.enquiry_id)))
        FROM enquiries e
        WHERE e.status IN ('New', 'Pending', 'Assigned', 'In Progress', 'Reopened')
          AND (p_category_id IS NULL OR e.category_id = p_category_id)
          AND (p_date_from IS NULL OR e.created_at >= p_date_from)
          AND (p_date_to IS NULL OR e.created_at < p_date_to + interval '1 day')
          AND (p_source IS NULL OR e.source = p_source)
          AND (p_assigned_to_user_id IS NULL
               OR e.assigned_to_user_id = p_assigned_to_user_id);
    $$;
    ''',
    r'''
    CREATE OR REPLACE FUNCTION usp_report_resolved_enquiries(
        p_status text DEFAULT NULL, p_category_id integer DEFAULT NULL,
        p_date_from date DEFAULT NULL, p_date_to date DEFAULT NULL,
        p_source text DEFAULT NULL, p_assigned_to_user_id integer DEFAULT NULL)
    RETURNS jsonb LANGUAGE sql STABLE AS $$
        SELECT usp_api_success(jsonb_build_object(
            'title', 'Resolved Enquiry Report',
            'headers', jsonb_build_array('Enquiry Number', 'Date', 'Full Name', 'Mobile',
                                         'Category', 'Subject', 'Status', 'Assigned To'),
            'rows', COALESCE(jsonb_agg(usp_report_brief_row(e.enquiry_id)
                                       ORDER BY e.created_at), '[]'::jsonb),
            'total_records', count(e.enquiry_id)))
        FROM enquiries e
        WHERE e.status = 'Resolved'
          AND (p_category_id IS NULL OR e.category_id = p_category_id)
          AND (p_date_from IS NULL OR e.created_at >= p_date_from)
          AND (p_date_to IS NULL OR e.created_at < p_date_to + interval '1 day')
          AND (p_source IS NULL OR e.source = p_source)
          AND (p_assigned_to_user_id IS NULL
               OR e.assigned_to_user_id = p_assigned_to_user_id);
    $$;
    ''',
]

# --------------------------------------------------------------------------
# 7. Installation / removal
# --------------------------------------------------------------------------
ALL_STATEMENTS = [
    *HELPER_PROCEDURES,
    *PUBLIC_PROCEDURES,
    *STAFF_PROCEDURES,
    *DASHBOARD_PROCEDURES,
    *ADMIN_PROCEDURES,
    *REPORT_PROCEDURES,
]

DROP_STATEMENTS = [
    "DROP TRIGGER IF EXISTS trg_enquiries_updated_at ON enquiries;",
    "DROP TRIGGER IF EXISTS trg_users_updated_at ON users;",
    *(
        f"DROP FUNCTION IF EXISTS {signature};"
        for signature in (
            "usp_api_success(jsonb)",
            "usp_api_error(text, text)",
            "usp_normalize_mobile(text)",
            "usp_set_updated_at()",
            "usp_submit_visitor_enquiry(text, text, integer, text, text, text, text, text, text, integer)",
            "usp_submit_visitor_enquiry(text, text, integer, text, text, text, text, text, text)",
            "usp_issue_tracking_token(integer, integer)",
            "usp_track_by_reference(text, text, text)",
            "usp_track_status_by_reference(text, text)",
            "usp_lookup_enquiries_by_mobile(text, integer)",
            "usp_reissue_tracking_token(text, text, text, integer)",
            "usp_reissue_tracking_token(text, text, integer, text)",
            "usp_get_public_stats()",
            "usp_list_active_categories()",
            "usp_enquiry_row_json(integer)",
            "usp_allowed_status_transitions()",
            "usp_create_staff_enquiry(integer, text, text, integer, text, text, text, text, text, text, text)",
            "usp_list_enquiries(integer, boolean, text, text, integer, text, integer, date, date, integer, integer)",
            "usp_get_enquiry(integer, boolean, integer)",
            "usp_update_enquiry(integer, boolean, integer, text, text, text, text, text, integer, text, text, text)",
            "usp_assign_enquiry(integer, integer, integer, text, text)",
            "usp_update_enquiry_status(integer, boolean, integer, text, text, text)",
            "usp_add_enquiry_remark(integer, boolean, integer, text, text)",
            "usp_add_enquiry_attachment(integer, boolean, integer, text, text, text, bigint, text, text, text[], integer)",
            "usp_delete_enquiry_attachment(integer, boolean, integer, text)",
            "usp_get_dashboard_kpis(integer, boolean)",
            "usp_get_dashboard_charts(integer, boolean)",
            "usp_list_users(text)",
            "usp_create_user(text, text, text, text, integer, boolean, integer, text)",
            "usp_toggle_user_status(integer, integer, text)",
            "usp_list_categories(text)",
            "usp_create_category(text, text, integer, text)",
            "usp_toggle_category_status(integer, integer, text)",
            "usp_report_filtered_enquiries(text, integer, date, date, text, integer, boolean)",
            "usp_report_detail_row(integer)",
            "usp_report_brief_row(integer)",
            "usp_report_total_enquiries(text, integer, date, date, text, integer)",
            "usp_report_date_wise(text, integer, date, date, text, integer)",
            "usp_report_status_wise(text, integer, date, date, text, integer)",
            "usp_report_category_wise(text, integer, date, date, text, integer)",
            "usp_report_employee_wise(text, integer, date, date, text, integer)",
            "usp_report_source_wise(text, integer, date, date, text, integer)",
            "usp_report_pending_enquiries(text, integer, date, date, text, integer)",
            "usp_report_resolved_enquiries(text, integer, date, date, text, integer)",
        )
    ),
    # Legacy visitor-token table (removed with the token system).
    "DROP TABLE IF EXISTS visitor_tracking;",
]


def install_statements(session) -> None:
    """Create every procedure (idempotent — CREATE OR REPLACE)."""
    for statement in (PGCRYPTO_SQL, SEQUENCE_SQL, *TIMESTAMP_DEFAULTS_SQL, *ALL_STATEMENTS):
        session.execute(text(statement))


def drop_statements(session) -> None:
    """Remove every procedure (used when resetting a database)."""
    for statement in DROP_STATEMENTS:
        session.execute(text(statement))
