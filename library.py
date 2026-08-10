"""Library database application -- CMPT 354 mini project.

Creates the library database and provides the eight operations required of the
application layer: find, borrow, return, donate, find an event, register for an
event, volunteer, and ask a librarian for help.

Integrity is the database's job, not this module's.  Every business rule from
section 1.3 of the report is enforced by a CHECK constraint or a trigger in
SCHEMA_SQL / TRIGGERS_SQL below.  The functions here issue statements and let
sqlite3.IntegrityError propagate; they never re-check a rule in Python, because
a second copy of a rule is a copy that drifts.

Three of the eight operations have no table behind them -- the report's section
1.2 never describes donations, volunteering, or help requests -- so they are
mapped onto the existing schema.  The mappings are lossy, and knowingly so:

  * donate_item()   inserts Item + Copy and records the donor as a WishlistItem
                    row (status 'acquired', requested_by = the member).  A wish
                    list entry is meant to be material the library *may* acquire
                    later, so the row is being used against its stated meaning.

  * volunteer()     inserts an Employee row with job_title 'Volunteer - <role>'
                    and salary 0.  Consequences: staff counts, payroll sums and
                    supervision queries include volunteers unless they filter
                    job_title NOT LIKE 'Volunteer%'; and since Employee has no
                    member_id there is no reliable link back to the member who
                    volunteered -- this module matches on name plus phone, which
                    is not a key and can collide.

  * ask_librarian() does not persist anything.  It returns the librarians on
                    duty; the question is lost when the process exits.

Run `python library.py` for an interactive menu.
"""

import sqlite3
from datetime import date

DB_NAME = "library.db"


class LibraryError(Exception):
    """A request that cannot be carried out for a reason SQLite has no
    constraint for: an unknown member, no head librarian on file, and so on.
    Business rules are never raised from here -- the triggers raise those."""


# ---------------------------------------------------------------------------
# Schema: reflect relations in final schema 3.5
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
DROP TABLE IF EXISTS WishlistItem;
DROP TABLE IF EXISTS Registration;
DROP TABLE IF EXISTS Event;
DROP TABLE IF EXISTS Employee;
DROP TABLE IF EXISTS Room;
DROP TABLE IF EXISTS Fine;
DROP TABLE IF EXISTS Loan;
DROP TABLE IF EXISTS Copy;
DROP TABLE IF EXISTS Item;
DROP TABLE IF EXISTS ItemType;
DROP TABLE IF EXISTS Member;
DROP TABLE IF EXISTS PostalArea;


CREATE TABLE PostalArea (
    postal_code char(6) NOT NULL,
    city        text    NOT NULL,
    PRIMARY KEY (postal_code),
    CONSTRAINT postalCodeCheck CHECK (length(postal_code) = 6)
);


CREATE TABLE Member (
    member_id   integer NOT NULL,
    first_name  text    NOT NULL,
    last_name   text    NOT NULL,
    email       text    NOT NULL UNIQUE,          -- BR1
    phone       varchar(12) NOT NULL,             -- one phone per member (1.4)
    street      text    NOT NULL,
    postal_code char(6) NOT NULL,
    join_date   date    NOT NULL,
    status      text    NOT NULL DEFAULT 'active'
        CONSTRAINT memberStatusCheck CHECK (status IN ('active', 'suspended')),
    PRIMARY KEY (member_id),
    FOREIGN KEY (postal_code) REFERENCES PostalArea(postal_code) ON DELETE RESTRICT
);

CREATE TABLE ItemType (
    type_code       text    NOT NULL,
    type_name       text    NOT NULL UNIQUE,
    loan_period     integer NOT NULL
        CONSTRAINT loanPeriodCheck CHECK (loan_period > 0),
    daily_fine_rate decimal(6,2) NOT NULL
        CONSTRAINT dailyFineRateCheck CHECK (daily_fine_rate >= 0),
    PRIMARY KEY (type_code)
);

CREATE TABLE Item (
    item_id        integer NOT NULL,
    title          text    NOT NULL,
    creator        text,
    published_year integer
        CONSTRAINT publishedYearCheck
        CHECK (published_year IS NULL OR published_year BETWEEN 1000 AND 2100),
    language       text,
    type_code      text    NOT NULL,
    PRIMARY KEY (item_id),
    FOREIGN KEY (type_code) REFERENCES ItemType(type_code) ON DELETE RESTRICT
);

CREATE TABLE Copy (
    item_id          integer NOT NULL,
    copy_number      integer NOT NULL
        CONSTRAINT copyNumberCheck CHECK (copy_number > 0),
    acquisition_date date    NOT NULL,
    copy_status      text    NOT NULL DEFAULT 'available'
        CONSTRAINT copyStatusCheck CHECK (copy_status IN ('available', 'loaned', 'lost')),
    PRIMARY KEY (item_id, copy_number),
    FOREIGN KEY (item_id) REFERENCES Item(item_id) ON DELETE CASCADE
);

CREATE TABLE Loan (
    loan_id       integer NOT NULL,
    item_id       integer NOT NULL,
    copy_number   integer NOT NULL,
    member_id     integer NOT NULL,
    checkout_date date    NOT NULL,
    return_date   date,
    PRIMARY KEY (loan_id),
    CONSTRAINT uniqueCheckout UNIQUE (item_id, copy_number, checkout_date),
    CONSTRAINT returnDateCheck
        CHECK (return_date IS NULL OR return_date >= checkout_date),
    FOREIGN KEY (item_id, copy_number)
        REFERENCES Copy(item_id, copy_number) ON DELETE RESTRICT,
    FOREIGN KEY (member_id) REFERENCES Member(member_id) ON DELETE RESTRICT
);

CREATE TABLE Fine (
    loan_id       integer NOT NULL,
    amount        decimal(8,2) NOT NULL DEFAULT 0
        CONSTRAINT fineAmountCheck CHECK (amount >= 0),
    assessed_date date    NOT NULL,
    fine_status   text    NOT NULL DEFAULT 'outstanding'
        CONSTRAINT fineStatusCheck CHECK (fine_status IN ('outstanding', 'paid')),
    PRIMARY KEY (loan_id),
    FOREIGN KEY (loan_id) REFERENCES Loan(loan_id) ON DELETE CASCADE
);

CREATE TABLE Room (
    room_number integer NOT NULL,
    capacity    integer NOT NULL
        CONSTRAINT capacityCheck CHECK (capacity > 0),
    PRIMARY KEY (room_number)
);

CREATE TABLE Employee (
    employee_id   integer NOT NULL,
    first_name    text    NOT NULL,
    last_name     text    NOT NULL,
    job_title     text    NOT NULL,

    salary        decimal(10,2) NOT NULL
        CONSTRAINT salaryCheck CHECK (salary >= 0),

    phone         varchar(12) NOT NULL,
    supervisor_id integer
        CONSTRAINT noSelfSupervisionCheck
        CHECK (supervisor_id IS NULL OR supervisor_id <> employee_id),
    PRIMARY KEY (employee_id),
    FOREIGN KEY (supervisor_id) REFERENCES Employee(employee_id) ON DELETE RESTRICT
);

CREATE TABLE Event (
    event_id          integer NOT NULL,
    title             text    NOT NULL,
    event_category    text    NOT NULL
        CONSTRAINT eventCategoryCheck
        CHECK (event_category IN ('book club', 'author talk', 'art show',
                                  'film screening', 'workshop')),
    audience_category text    NOT NULL DEFAULT 'all ages'
        CONSTRAINT audienceCheck
        CHECK (audience_category IN ('children', 'teens', 'adults',
                                     'seniors', 'all ages')),
    start_datetime    timestamp NOT NULL,
    end_datetime      timestamp NOT NULL,
    max_attendees     integer NOT NULL
        CONSTRAINT maxAttendeesCheck CHECK (max_attendees > 0),
    room_number       integer NOT NULL,
    employee_id       integer NOT NULL,
    PRIMARY KEY (event_id),
    CONSTRAINT uniqueRoomStart UNIQUE (room_number, start_datetime),
    CONSTRAINT eventTimesCheck CHECK (end_datetime > start_datetime),
    FOREIGN KEY (room_number) REFERENCES Room(room_number) ON DELETE RESTRICT,
    FOREIGN KEY (employee_id) REFERENCES Employee(employee_id) ON DELETE RESTRICT
);

CREATE TABLE Registration (
    member_id         integer NOT NULL,
    event_id          integer NOT NULL,
    registration_date date    NOT NULL,
    attended          text    NOT NULL DEFAULT 'absent'
        CONSTRAINT attendanceCheck CHECK (attended IN ('absent', 'present')),
    PRIMARY KEY (member_id, event_id),
    FOREIGN KEY (member_id) REFERENCES Member(member_id) ON DELETE RESTRICT,
    FOREIGN KEY (event_id) REFERENCES Event(event_id) ON DELETE RESTRICT
);

CREATE TABLE WishlistItem (
    request_id       integer NOT NULL,
    proposed_title   text    NOT NULL,
    proposed_creator text,
    proposed_format  text
        CONSTRAINT proposedFormatCheck
        CHECK (proposed_format IS NULL OR
               proposed_format IN ('print book', 'online book', 'magazine',
                                   'scientific journal', 'audio record')),
    cost             decimal(8,2)
        CONSTRAINT costCheck CHECK (cost IS NULL OR cost >= 0),
    requested_date   date    NOT NULL,
    status           text    NOT NULL DEFAULT 'pending'
        CONSTRAINT wishlistStatusCheck
        CHECK (status IN ('pending', 'approved', 'rejected', 'acquired')),
    requested_by     integer,
    reviewed_by      integer,
    PRIMARY KEY (request_id),
    FOREIGN KEY (requested_by) REFERENCES Member(member_id) ON DELETE RESTRICT,
    FOREIGN KEY (reviewed_by) REFERENCES Employee(employee_id) ON DELETE RESTRICT
);"""

# ---------------------------------------------------------------------------
# Triggers to match business rules
# ---------------------------------------------------------------------------
TRIGGERS_SQL = """
-- Each trigger is named after the business rule it enforces.  The DROPs let
-- this cell be re-run, and must come first: there is no IF NOT EXISTS form
-- of the create statement that would replace an existing definition.

DROP TRIGGER IF EXISTS trig_BR11_suspendedMemberNoBorrow;
DROP TRIGGER IF EXISTS trig_BR10_LoanMax;
DROP TRIGGER IF EXISTS trig_BR6_CopyAlreadyLoaned;
DROP TRIGGER IF EXISTS trig_BR6_CopyNotAvailable;
DROP TRIGGER IF EXISTS trig_BR6_SetCopyLoaned;
DROP TRIGGER IF EXISTS trig_LoanReturnMax;
DROP TRIGGER IF EXISTS trig_BR6_SetCopyAvailable;
DROP TRIGGER IF EXISTS trig_BR13_FineWhenOverdue;
DROP TRIGGER IF EXISTS trig_BR13_FineAmount;
DROP TRIGGER IF EXISTS trig_MaxAttendeesExceedRoomCapacity;
DROP TRIGGER IF EXISTS trig_BR15_TimeslotOccupied;
DROP TRIGGER IF EXISTS trig_BR15_RoomOccupied;
DROP TRIGGER IF EXISTS trig_BR11_NoRegSuspended;
DROP TRIGGER IF EXISTS trig_BR18_RegMaxCapacity;
DROP TRIGGER IF EXISTS trig_BR19_OneHeadInsertion;
DROP TRIGGER IF EXISTS trig_BR19_OneHeadUpdate;
DROP TRIGGER IF EXISTS trig_BR19_NoSelfSupervision;

CREATE TRIGGER trig_BR11_suspendedMemberNoBorrow
BEFORE INSERT ON Loan
FOR EACH ROW
WHEN (SELECT status FROM Member WHERE member_id = NEW.member_id) <> 'active'
BEGIN
    SELECT RAISE(ABORT, 'BR11: a suspended member may not borrow');
END;

CREATE TRIGGER trig_BR10_LoanMax
BEFORE INSERT ON Loan
FOR EACH ROW
WHEN (SELECT count(*) FROM Loan
      WHERE member_id = NEW.member_id AND return_date IS NULL) >= 5
BEGIN
    SELECT RAISE(ABORT, 'BR10: a member may have at most five items on loan at once');
END;

CREATE TRIGGER trig_BR6_CopyAlreadyLoaned
BEFORE INSERT ON Loan
FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM Loan
             WHERE item_id = NEW.item_id
               AND copy_number = NEW.copy_number
               AND return_date IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'BR6: that copy is already on loan');
END;

CREATE TRIGGER trig_BR6_CopyNotAvailable
BEFORE INSERT ON Loan
FOR EACH ROW
WHEN (SELECT copy_status FROM Copy
      WHERE item_id = NEW.item_id AND copy_number = NEW.copy_number) <> 'available'
BEGIN
    SELECT RAISE(ABORT, 'BR6: that copy is not available for loan');
END;

CREATE TRIGGER trig_BR6_SetCopyLoaned
AFTER INSERT ON Loan
FOR EACH ROW
BEGIN
    UPDATE Copy SET copy_status = 'loaned'
    WHERE item_id = NEW.item_id AND copy_number = NEW.copy_number;
END;

CREATE TRIGGER trig_LoanReturnMax
BEFORE UPDATE OF return_date ON Loan
FOR EACH ROW
WHEN OLD.return_date IS NOT NULL AND NEW.return_date IS NOT OLD.return_date
BEGIN
    SELECT RAISE(ABORT, 'a loan that has already been returned may not be returned again');
END;

CREATE TRIGGER trig_BR6_SetCopyAvailable
AFTER UPDATE OF return_date ON Loan
FOR EACH ROW
WHEN OLD.return_date IS NULL AND NEW.return_date IS NOT NULL
BEGIN
    UPDATE Copy SET copy_status = 'available'
    WHERE item_id = NEW.item_id
      AND copy_number = NEW.copy_number
      AND copy_status = 'loaned';
END;

CREATE TRIGGER trig_BR13_FineWhenOverdue
BEFORE INSERT ON Fine
FOR EACH ROW
WHEN (SELECT l.return_date IS NULL
             OR julianday(l.return_date) <=
                julianday(date(l.checkout_date, '+' || t.loan_period || ' days'))
      FROM Loan l
      JOIN Item i     ON i.item_id   = l.item_id
      JOIN ItemType t ON t.type_code = i.type_code
      WHERE l.loan_id = NEW.loan_id)
BEGIN
    SELECT RAISE(ABORT, 'BR13: a fine may only be assessed on a loan returned after its due date');
END;

CREATE TRIGGER trig_BR13_FineAmount
AFTER INSERT ON Fine
FOR EACH ROW
BEGIN
    UPDATE Fine
    SET amount = (SELECT round((julianday(l.return_date)
                                - julianday(date(l.checkout_date,
                                                 '+' || t.loan_period || ' days')))
                               * t.daily_fine_rate, 2)
                  FROM Loan l
                  JOIN Item i     ON i.item_id   = l.item_id
                  JOIN ItemType t ON t.type_code = i.type_code
                  WHERE l.loan_id = NEW.loan_id)
    WHERE loan_id = NEW.loan_id;
END;

CREATE TRIGGER trig_MaxAttendeesExceedRoomCapacity
BEFORE INSERT ON Event
FOR EACH ROW
WHEN NEW.max_attendees >
     (SELECT capacity FROM Room WHERE room_number = NEW.room_number)
BEGIN
    SELECT RAISE(ABORT, 'max_attendees may not exceed the capacity of the room');
END;

CREATE TRIGGER trig_BR15_TimeslotOccupied
BEFORE INSERT ON Event
FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM Event e
             WHERE e.room_number    = NEW.room_number
               AND e.start_datetime < NEW.end_datetime
               AND NEW.start_datetime < e.end_datetime)
BEGIN
    SELECT RAISE(ABORT, 'BR15: another event already occupies that room at an overlapping time');
END;

CREATE TRIGGER trig_BR15_RoomOccupied
BEFORE UPDATE OF room_number, start_datetime, end_datetime ON Event
FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM Event e
             WHERE e.event_id      <> NEW.event_id
               AND e.room_number    = NEW.room_number
               AND e.start_datetime < NEW.end_datetime
               AND NEW.start_datetime < e.end_datetime)
BEGIN
    SELECT RAISE(ABORT, 'BR15: another event already occupies that room at an overlapping time');
END;

CREATE TRIGGER trig_BR11_NoRegSuspended
BEFORE INSERT ON Registration
FOR EACH ROW
WHEN (SELECT status FROM Member WHERE member_id = NEW.member_id) <> 'active'
BEGIN
    SELECT RAISE(ABORT, 'BR11: a suspended member may not register for events');
END;

CREATE TRIGGER trig_BR18_RegMaxCapacity
BEFORE INSERT ON Registration
FOR EACH ROW
WHEN (SELECT count(*) FROM Registration WHERE event_id = NEW.event_id)
     >= (SELECT max_attendees FROM Event WHERE event_id = NEW.event_id)
BEGIN
    SELECT RAISE(ABORT, 'BR18: the event has reached its maximum number of attendees');
END;

CREATE TRIGGER trig_BR19_OneHeadInsertion
BEFORE INSERT ON Employee
FOR EACH ROW
WHEN NEW.supervisor_id IS NULL
     AND EXISTS (SELECT 1 FROM Employee WHERE supervisor_id IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'BR19: only the head librarian may have no supervisor');
END;

CREATE TRIGGER trig_BR19_OneHeadUpdate
BEFORE UPDATE OF supervisor_id ON Employee
FOR EACH ROW
WHEN NEW.supervisor_id IS NULL
     AND EXISTS (SELECT 1 FROM Employee
                 WHERE supervisor_id IS NULL AND employee_id <> NEW.employee_id)
BEGIN
    SELECT RAISE(ABORT, 'BR19: only the head librarian may have no supervisor');
END;

CREATE TRIGGER trig_BR19_NoSelfSupervision
BEFORE UPDATE OF supervisor_id ON Employee
FOR EACH ROW
WHEN NEW.supervisor_id IS NOT NULL
     AND EXISTS (WITH RECURSIVE chain(id) AS (
                     SELECT NEW.supervisor_id
                     UNION
                     SELECT e.supervisor_id FROM Employee e
                     JOIN chain c ON e.employee_id = c.id
                     WHERE e.supervisor_id IS NOT NULL)
                 SELECT 1 FROM chain WHERE id = NEW.employee_id)
BEGIN
    SELECT RAISE(ABORT, 'BR19: an employee may not end up supervising themselves');
END;
"""


# ---------------------------------------------------------------------------
# Connection handling
# ---------------------------------------------------------------------------

def connect(db_name=DB_NAME):
    conn = sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_name=DB_NAME):
    with connect(db_name) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.executescript(TRIGGERS_SQL)
    return db_name


def _is_initialised(conn):
    row = conn.execute(
        "SELECT count(*) AS n FROM sqlite_master WHERE type = 'table'").fetchone()
    return row["n"] > 0


# ---------------------------------------------------------------------------
# Item Type used to derive due date. 
# ---------------------------------------------------------------------------

_LOAN_JOIN = """
      FROM Loan l
      JOIN Item i     ON i.item_id   = l.item_id
      JOIN ItemType t ON t.type_code = i.type_code
"""

_DUE_DATE = "date(l.checkout_date, '+' || t.loan_period || ' days')"


def _today():
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# 1. Allow user to find an item in the library
# ---------------------------------------------------------------------------

def find_items(conn, keyword=""):
    """Items whose title or creator matches, with how many copies are free."""
    pattern = "%" + keyword + "%"
    return conn.execute("""
        SELECT i.item_id, i.title, i.creator, i.published_year, i.language,
               t.type_name, t.loan_period, t.daily_fine_rate,
               (SELECT count(*) FROM Copy c
                 WHERE c.item_id = i.item_id) AS total_copies,
               (SELECT count(*) FROM Copy c
                 WHERE c.item_id = i.item_id
                   AND c.copy_status = 'available') AS available_copies
          FROM Item i
          JOIN ItemType t ON t.type_code = i.type_code
         WHERE i.title LIKE ? OR ifnull(i.creator, '') LIKE ?
         ORDER BY i.title
    """, (pattern, pattern)).fetchall()

def find_available_copies(conn, item_id):
    """Copies of an item that are free to borrow right now."""
    return conn.execute("""
        SELECT c.item_id, c.copy_number, c.acquisition_date, c.copy_status
          FROM Copy c
         WHERE c.item_id = ?
           AND c.copy_status = 'available'
           AND NOT EXISTS (SELECT 1 FROM Loan l
                            WHERE l.item_id     = c.item_id
                              AND l.copy_number = c.copy_number
                              AND l.return_date IS NULL)
         ORDER BY c.copy_number
    """, (item_id,)).fetchall()

# ---------------------------------------------------------------------------
# 2. Borrow an item from the library
# ---------------------------------------------------------------------------

def borrow_item(conn, member_id, item_id, copy_number, checkout_date=None):
    """Check a copy out.  Returns (loan_id, due_date).

    BR6 (copy free), BR10 (five-loan cap) and BR11 (not suspended) are enforced
    by triggers; a violation surfaces as sqlite3.IntegrityError carrying the
    trigger's own message.
    """
    checkout_date = checkout_date or _today()
    with conn:
        cur = conn.execute("""
            INSERT INTO Loan (item_id, copy_number, member_id, checkout_date)
            VALUES (?, ?, ?, ?)
        """, (item_id, copy_number, member_id, checkout_date))
        loan_id = cur.lastrowid
    return loan_id, due_date_of(conn, loan_id)


def due_date_of(conn, loan_id):
    """The derived due date of a loan: checkout date plus the type's period."""
    row = conn.execute(
        "SELECT " + _DUE_DATE + " AS due_date" + _LOAN_JOIN +
        " WHERE l.loan_id = ?", (loan_id,)).fetchone()
    if row is None:
        raise LibraryError("There is no loan with id %s." % loan_id)
    return row["due_date"]


def outstanding_loans(conn, member_id):
    """A member's un-returned loans, with derived due dates and days overdue."""
    return conn.execute(
        "SELECT l.loan_id, l.item_id, l.copy_number, i.title,"
        "       l.checkout_date, " + _DUE_DATE + " AS due_date,"
        "       CAST(julianday('now') - julianday(" + _DUE_DATE +
        "            ) AS integer) AS days_overdue" + _LOAN_JOIN +
        " WHERE l.member_id = ? AND l.return_date IS NULL"
        " ORDER BY due_date", (member_id,)).fetchall()


# ---------------------------------------------------------------------------
# 3. Return a borrowed item
# ---------------------------------------------------------------------------

def return_item(conn, loan_id, return_date=None):
    """Return a copy.  Returns (due_date, days_overdue, fine_amount_or_None).

    The BR13 trigger refuses a fine on a loan that was not late and computes the
    amount itself, so this only decides whether to create the Fine row at all
    and then reads back what the trigger worked out.
    """
    return_date = return_date or _today()
    with conn:
        cur = conn.execute(
            "UPDATE Loan SET return_date = ? WHERE loan_id = ? "
            "AND return_date IS NULL", (return_date, loan_id))
        if cur.rowcount == 0:
            raise LibraryError(
                "Loan %s does not exist or has already been returned." % loan_id)

        row = conn.execute(
            "SELECT " + _DUE_DATE + " AS due_date,"
            "       CAST(julianday(l.return_date) - julianday(" + _DUE_DATE +
            "            ) AS integer) AS days_overdue" + _LOAN_JOIN +
            " WHERE l.loan_id = ?", (loan_id,)).fetchone()

        fine = None
        if row["days_overdue"] > 0:
            conn.execute(
                "INSERT INTO Fine (loan_id, assessed_date) VALUES (?, ?)",
                (loan_id, return_date))
            fine = conn.execute(
                "SELECT amount FROM Fine WHERE loan_id = ?",
                (loan_id,)).fetchone()["amount"]

    return row["due_date"], row["days_overdue"], fine


# ---------------------------------------------------------------------------
# 4. Donate an item to the library
# ---------------------------------------------------------------------------

def donate_item(conn, member_id, title, type_code, creator=None,
                published_year=None, language=None, donated_date=None):
    """Accept a donated item.  Returns (item_id, copy_number, request_id).

    See the module docstring: the schema has no Donation relation, so this
    creates the Item and its first Copy and records the donor as a WishlistItem
    row.  All three inserts are one transaction -- a failure leaves nothing.
    """
    donated_date = donated_date or _today()

    fmt = conn.execute(
        "SELECT type_name FROM ItemType WHERE type_code = ?",
        (type_code,)).fetchone()
    if fmt is None:
        raise LibraryError(
            "There is no item type '%s'.  Known types: %s" %
            (type_code, ", ".join(r["type_code"] for r in
                                  conn.execute("SELECT type_code FROM ItemType"))))

    with conn:
        cur = conn.execute("""
            INSERT INTO Item (title, creator, published_year, language, type_code)
            VALUES (?, ?, ?, ?, ?)
        """, (title, creator, published_year, language, type_code))
        item_id = cur.lastrowid

        conn.execute("""
            INSERT INTO Copy (item_id, copy_number, acquisition_date, copy_status)
            VALUES (?, 1, ?, 'available')
        """, (item_id, donated_date))

        cur = conn.execute("""
            INSERT INTO WishlistItem (proposed_title, proposed_creator,
                                      proposed_format, cost, requested_date,
                                      status, requested_by)
            VALUES (?, ?, ?, 0, ?, 'acquired', ?)
        """, (title, creator, fmt["type_name"], donated_date, member_id))
        request_id = cur.lastrowid

    return item_id, 1, request_id


# ---------------------------------------------------------------------------
# 5. Find an event in the library
# ---------------------------------------------------------------------------

def find_events(conn, keyword=None, upcoming_only=True):
    """Events matching a keyword, with seats left."""
    pattern = "%" + (keyword or "") + "%"
    return conn.execute("""
        SELECT e.event_id, e.title, e.event_category, e.audience_category,
               e.start_datetime, e.end_datetime, e.max_attendees,
               e.room_number, r.capacity,
               (SELECT count(*) FROM Registration g
                 WHERE g.event_id = e.event_id) AS registered,
               e.max_attendees - (SELECT count(*) FROM Registration g
                                   WHERE g.event_id = e.event_id) AS seats_left
          FROM Event e
          JOIN Room r ON r.room_number = e.room_number
         WHERE (e.title LIKE ? OR e.event_category LIKE ?
                OR e.audience_category LIKE ?)
           AND (? = 0 OR e.start_datetime >= datetime('now'))
         ORDER BY e.start_datetime
    """, (pattern, pattern, pattern, 1 if upcoming_only else 0)).fetchall()


# ---------------------------------------------------------------------------
# 6. Register for an event
# ---------------------------------------------------------------------------

def register_for_event(conn, member_id, event_id, registration_date=None):
    """Register a member.  BR11 (not suspended) and BR18 (capacity) are trigger
    rules; BR17 (at most once) is the composite primary key."""
    registration_date = registration_date or _today()
    with conn:
        conn.execute("""
            INSERT INTO Registration (member_id, event_id, registration_date)
            VALUES (?, ?, ?)
        """, (member_id, event_id, registration_date))
    return member_id, event_id


# ---------------------------------------------------------------------------
# 7. Volunteer for the library
# ---------------------------------------------------------------------------

def volunteer(conn, member_id, role="General"):
    """Sign a member up as a volunteer.  Returns the new employee_id.

    See the module docstring: volunteers are stored as Employee rows, which is
    the only staff-shaped table in the schema.
    """
    member = conn.execute("""
        SELECT first_name, last_name, phone FROM Member WHERE member_id = ?
    """, (member_id,)).fetchone()
    if member is None:
        raise LibraryError("There is no member with id %s." % member_id)

    # BR19: exactly one employee may have no supervisor, so a volunteer needs
    # one.  The head librarian is the employee with no supervisor.
    head = conn.execute(
        "SELECT employee_id FROM Employee WHERE supervisor_id IS NULL").fetchone()
    if head is None:
        raise LibraryError(
            "No head librarian is on file yet, so a volunteer has nobody to "
            "report to.  Add the head librarian before signing up volunteers.")

    already = conn.execute("""
        SELECT employee_id FROM Employee
         WHERE first_name = ? AND last_name = ? AND phone = ?
           AND job_title LIKE 'Volunteer%'
    """, (member["first_name"], member["last_name"], member["phone"])).fetchone()
    if already is not None:
        raise LibraryError(
            "%s %s already volunteers here (employee %s)." %
            (member["first_name"], member["last_name"], already["employee_id"]))

    with conn:
        cur = conn.execute("""
            INSERT INTO Employee (first_name, last_name, job_title, salary,
                                  phone, supervisor_id)
            VALUES (?, ?, ?, 0, ?, ?)
        """, (member["first_name"], member["last_name"],
              "Volunteer - " + role, member["phone"], head["employee_id"]))
    return cur.lastrowid


# ---------------------------------------------------------------------------
# 8. Ask for help from a librarian
# ---------------------------------------------------------------------------

def ask_librarian(conn, question=""):
    """Return the librarians who can help.

    See the module docstring: there is no relation for help requests, so the
    question itself is not stored anywhere.
    """
    return conn.execute("""
        SELECT employee_id, first_name, last_name, job_title, phone
          FROM Employee
         WHERE job_title LIKE '%Librarian%'
           AND job_title NOT LIKE 'Volunteer%'
         ORDER BY job_title, last_name
    """).fetchall()


# ---------------------------------------------------------------------------
# Command line interface
# ---------------------------------------------------------------------------

MENU = """
  1. Find an item in the library
  2. Borrow an item from the library
  3. Return a borrowed item
  4. Donate an item to the library
  5. Find an event in the library
  6. Register for an event in the library
  7. Volunteer for the library
  8. Ask for help from a librarian
  0. Quit
"""


def _show(rows, columns=None):
    """Print rows as an aligned table."""
    rows = list(rows)
    if not rows:
        print("  (nothing found)")
        return
    columns = columns or rows[0].keys()
    widths = [max(len(str(c)), max(len(str(r[c])) for r in rows)) for c in columns]
    print("  " + "  ".join(str(c).ljust(w) for c, w in zip(columns, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for r in rows:
        print("  " + "  ".join(str(r[c]).ljust(w) for c, w in zip(columns, widths)))


def _ask(prompt, cast=str, optional=False):
    raw = input(prompt).strip()
    if not raw:
        if optional:
            return None
        raise LibraryError("A value is required.")
    return cast(raw)


def _do_find_item(conn):
    _show(find_items(conn, _ask("  Title or creator: ", optional=True) or ""),
          ["item_id", "title", "creator", "type_name",
           "total_copies", "available_copies"])


def _do_borrow(conn):
    member_id = _ask("  Member id: ", int)
    item_id = _ask("  Item id: ", int)
    free = find_available_copies(conn, item_id)
    if not free:
        raise LibraryError("No copies of item %s are available." % item_id)
    _show(free, ["item_id", "copy_number", "acquisition_date"])
    copy_number = _ask("  Copy number: ", int)
    loan_id, due = borrow_item(conn, member_id, item_id, copy_number)
    print("  Loan %s created.  Due back %s." % (loan_id, due))


def _do_return(conn):
    member_id = _ask("  Member id: ", int)
    loans = outstanding_loans(conn, member_id)
    if not loans:
        raise LibraryError("Member %s has nothing on loan." % member_id)
    _show(loans, ["loan_id", "title", "checkout_date", "due_date", "days_overdue"])
    loan_id = _ask("  Loan id to return: ", int)
    due, overdue, fine = return_item(conn, loan_id)
    if fine is None:
        print("  Returned on time (due %s).  No fine." % due)
    else:
        print("  Returned %s day(s) late (due %s).  Fine assessed: %.2f"
              % (overdue, due, fine))


def _do_donate(conn):
    member_id = _ask("  Your member id: ", int)
    title = _ask("  Title: ")
    _show(conn.execute("SELECT type_code, type_name FROM ItemType").fetchall())
    type_code = _ask("  Item type code: ")
    creator = _ask("  Creator (optional): ", optional=True)
    year = _ask("  Published year (optional): ", int, optional=True)
    language = _ask("  Language (optional): ", optional=True)
    item_id, copy_number, request_id = donate_item(
        conn, member_id, title, type_code, creator, year, language)
    print("  Thank you.  Catalogued as item %s copy %s (donation record %s)."
          % (item_id, copy_number, request_id))


def _do_find_event(conn):
    keyword = _ask("  Title, category or audience: ", optional=True)
    _show(find_events(conn, keyword),
          ["event_id", "title", "event_category", "audience_category",
           "start_datetime", "room_number", "seats_left"])


def _do_register(conn):
    member_id = _ask("  Member id: ", int)
    event_id = _ask("  Event id: ", int)
    register_for_event(conn, member_id, event_id)
    print("  Member %s is registered for event %s." % (member_id, event_id))


def _do_volunteer(conn):
    member_id = _ask("  Member id: ", int)
    role = _ask("  Role (optional): ", optional=True) or "General"
    employee_id = volunteer(conn, member_id, role)
    print("  Signed up as volunteer (employee %s, role %s)." % (employee_id, role))


def _do_ask_librarian(conn):
    question = _ask("  What do you need help with? ", optional=True) or ""
    librarians = ask_librarian(conn, question)
    if not librarians:
        raise LibraryError("No librarians are on file.")
    print("  These librarians can help:")
    _show(librarians, ["employee_id", "first_name", "last_name",
                       "job_title", "phone"])
    print("  (Note: the question itself is not stored -- the schema has no"
          " relation for help requests.)")


ACTIONS = {
    "1": _do_find_item,
    "2": _do_borrow,
    "3": _do_return,
    "4": _do_donate,
    "5": _do_find_event,
    "6": _do_register,
    "7": _do_volunteer,
    "8": _do_ask_librarian,
}


def main():
    conn = connect()
    if not _is_initialised(conn):
        print("Empty database -- creating tables and triggers.")
        conn.close()
        init_db()
        conn = connect()

    print("\nLibrary database application.")
    while True:
        print(MENU)
        try:
            choice = input("  Choose an option: ").strip()
        except EOFError:
            break
        if choice == "0":
            break
        action = ACTIONS.get(choice)
        if action is None:
            print("  '%s' is not one of the options." % choice)
            continue
        try:
            action(conn)
        except (sqlite3.Error, LibraryError) as err:
            # The triggers raise messages that already read as sentences
            # ("BR10: a member may have at most five items on loan at once"),
            # so print what the database said and go back to the menu.
            print("  %s" % err)
        except EOFError:
            break
    conn.close()
    print("  Goodbye.")


if __name__ == "__main__":
    main()
