"""Library database set-up -- CMPT 354 mini project.

Builds the database the application runs on: the twelve relations of section 3.5
as SCHEMA_SQL, the seventeen triggers enforcing the business rules of section 1.3
as TRIGGERS_SQL, and the seed data as DATA_SQL.

    python library.py

drops and recreates every table and trigger, then loads DATA_SQL.

The user-facing operations live in user.py, which imports connect() and
is_initialised() from here.  Nothing in this module imports user.py, so the
dependency runs one way only.

Integrity is the database's job.  Every business rule from section 1.3 is a CHECK
constraint or a trigger below, never a check written in Python, because a second
copy of a rule is a copy that drifts.
"""

import sqlite3
from contextlib import closing

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
    member_id   integer NOT NULL UNIQUE,
    first_name  text    NOT NULL,
    last_name   text    NOT NULL,
    email       text    NOT NULL UNIQUE,
    phone       varchar(12) NOT NULL,
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
    employee_id   integer NOT NULL UNIQUE,
    first_name    text    NOT NULL,
    last_name     text    NOT NULL,
    job_title     text    NOT NULL,

    salary        decimal(10,2) NOT NULL
        CONSTRAINT salaryCheck CHECK (salary >= 0),

    phone         varchar(12) NOT NULL,
    -- Nullable on purpose: BR19 gives the head librarian no supervisor, and
    -- NULL here is what marks that one row.  A NOT NULL column would make the
    -- head librarian unrepresentable and leave the BR19 triggers unreachable.
    supervisor_id integer
        CONSTRAINT noSelfSupervisionCheck CHECK (supervisor_id IS NULL OR supervisor_id <> employee_id),
    PRIMARY KEY (employee_id),
    FOREIGN KEY (supervisor_id) REFERENCES Employee(employee_id) ON DELETE RESTRICT
);

CREATE TABLE Event (
    event_id          integer NOT NULL,
    title             text    NOT NULL,
    event_category    text    NOT NULL
        CONSTRAINT eventCategoryCheck
        CHECK (event_category IN ('book club', 'author talk', 'art show', 'film screening', 'workshop')),
    audience_category text    NOT NULL DEFAULT 'all ages'
        CONSTRAINT audienceCheck
        CHECK (audience_category IN ('children', 'teens', 'adults', 'seniors', 'all ages')),
    start_datetime    timestamp NOT NULL,
    end_datetime      timestamp NOT NULL,
    max_attendees     integer NOT NULL
        CONSTRAINT maxAttendeesCheck CHECK (max_attendees > 0),
    room_number       integer NOT NULL,
    employee_id       integer NOT NULL UNIQUE,
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
    proposed_format  text NOT NULL
        CONSTRAINT proposedFormatCheck CHECK (proposed_format IN ('print book', 'online book', 'magazine',
                                                                    'scientific journal', 'audio record')),
    cost             decimal(8,2)
        CONSTRAINT costCheck CHECK (cost IS NULL OR cost >= 0),
    requested_date   date    NOT NULL,
    status           text    NOT NULL DEFAULT 'pending'
        CONSTRAINT wishlistStatusCheck CHECK (status IN ('pending', 'approved', 'rejected', 'acquired')),
    requested_by     integer,
    reviewed_by      integer,
    PRIMARY KEY (request_id),
    FOREIGN KEY (requested_by) REFERENCES Member(member_id) ON DELETE RESTRICT,
    FOREIGN KEY (reviewed_by) REFERENCES Employee(employee_id) ON DELETE RESTRICT
);"""

# ---------------------------------------------------------------------------
# Triggers to address business rules
# ---------------------------------------------------------------------------
TRIGGERS_SQL = """
CREATE TRIGGER suspendedMemberCannotBorrow
BEFORE INSERT ON Loan
FOR EACH ROW
WHEN (SELECT status FROM Member WHERE member_id = NEW.member_id) <> 'active'
BEGIN
    SELECT RAISE(ABORT, 'BR11: a suspended member may not borrow');
END;

CREATE TRIGGER LoanMax
BEFORE INSERT ON Loan
FOR EACH ROW
WHEN (SELECT count(*) FROM Loan
      WHERE member_id = NEW.member_id AND return_date IS NULL) >= 5
BEGIN
    SELECT RAISE(ABORT, 'BR10: a member may have at most five items on loan at once');
END;

CREATE TRIGGER CopyAlreadyLoaned
BEFORE INSERT ON Loan
FOR EACH ROW
WHEN EXISTS (SELECT * FROM Loan
             WHERE item_id = NEW.item_id AND copy_number = NEW.copy_number AND return_date IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'BR6: A copy is on loan at the moment');
END;

CREATE TRIGGER CopyNotAvailable
BEFORE INSERT ON Loan
FOR EACH ROW
WHEN (SELECT copy_status FROM Copy
      WHERE item_id = NEW.item_id AND copy_number = NEW.copy_number) <> 'available'
BEGIN
    SELECT RAISE(ABORT, 'BR6: that copy is not available for loan');
END;

CREATE TRIGGER SetCopyLoaned
AFTER INSERT ON Loan
FOR EACH ROW
BEGIN
    UPDATE Copy SET copy_status = 'loaned'
    WHERE item_id = NEW.item_id AND copy_number = NEW.copy_number;
END;

CREATE TRIGGER LoanReturnMax
BEFORE UPDATE OF return_date ON Loan
FOR EACH ROW
WHEN OLD.return_date IS NOT NULL AND NEW.return_date IS NOT OLD.return_date
BEGIN
    SELECT RAISE(ABORT, 'loan has already been returned');
END;

CREATE TRIGGER SetCopyAvailable
AFTER UPDATE OF return_date ON Loan
FOR EACH ROW
WHEN OLD.return_date IS NULL AND NEW.return_date IS NOT NULL
BEGIN
    UPDATE Copy SET copy_status = 'available'
    WHERE item_id = NEW.item_id
      AND copy_number = NEW.copy_number
      AND copy_status = 'loaned';
END;

CREATE TRIGGER OverdueCheck
BEFORE INSERT ON Fine
FOR EACH ROW
WHEN (SELECT l.return_date IS NULL OR l.return_date <= date(l.checkout_date, '+' || t.loan_period || ' days')
      FROM Loan l
      JOIN Item i     ON i.item_id   = l.item_id
      JOIN ItemType t ON t.type_code = i.type_code
      WHERE l.loan_id = NEW.loan_id)
BEGIN
    SELECT RAISE(ABORT, 'Loan must be overdue to assess a fine for the item');
END;

CREATE TRIGGER FineAmount
AFTER INSERT ON Fine
FOR EACH ROW
BEGIN
    UPDATE Fine
    SET amount = (SELECT round((julianday(l.return_date) - julianday(date(l.checkout_date, '+' || t.loan_period || ' days')))
                               * t.daily_fine_rate, 2)
                  FROM Loan l
                  JOIN Item i     ON i.item_id   = l.item_id
                  JOIN ItemType t ON t.type_code = i.type_code
                  WHERE l.loan_id = NEW.loan_id)
    WHERE loan_id = NEW.loan_id;
END;

CREATE TRIGGER MaxAttendeesExceedRoomCapacity
BEFORE INSERT ON Event
FOR EACH ROW
WHEN NEW.max_attendees > (SELECT capacity FROM Room WHERE room_number = NEW.room_number)
BEGIN
    SELECT RAISE(ABORT, 'Maximum attendees may not exceed the capacity of the room');
END;

CREATE TRIGGER TimeslotOccupied
BEFORE INSERT ON Event
FOR EACH ROW
WHEN EXISTS (SELECT * FROM Event e
             WHERE e.room_number    = NEW.room_number
               AND e.start_datetime < NEW.end_datetime
               AND NEW.start_datetime < e.end_datetime)
BEGIN
    SELECT RAISE(ABORT, 'BR15: two events may not occupy the room at overlapping times');
END;

CREATE TRIGGER RoomOccupied
BEFORE UPDATE OF room_number, start_datetime, end_datetime ON Event
FOR EACH ROW
WHEN EXISTS (SELECT * FROM Event e
             WHERE e.event_id      <> NEW.event_id
               AND e.room_number    = NEW.room_number
               AND e.start_datetime < NEW.end_datetime
               AND NEW.start_datetime < e.end_datetime)
BEGIN
    SELECT RAISE(ABORT, 'BR15: an event may not occupy the room at overlapping times');
END;

CREATE TRIGGER NoRegSuspended
BEFORE INSERT ON Registration
FOR EACH ROW
WHEN (SELECT status FROM Member WHERE member_id = NEW.member_id) <> 'active'
BEGIN
    SELECT RAISE(ABORT, 'BR11: a suspended member may not register for events');
END;

CREATE TRIGGER RegMaxCapacity
BEFORE INSERT ON Registration
FOR EACH ROW
WHEN (SELECT count(*) FROM Registration WHERE event_id = NEW.event_id)
     >= (SELECT max_attendees FROM Event WHERE event_id = NEW.event_id)
BEGIN
    SELECT RAISE(ABORT, 'BR18: the event has reached its maximum number of attendees');
END;

CREATE TRIGGER OneHead
BEFORE INSERT ON Employee
FOR EACH ROW
WHEN NEW.supervisor_id IS NULL
     AND EXISTS (SELECT * FROM Employee WHERE supervisor_id IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'BR19: every employee must report to exactly one supervisor');
END;

CREATE TRIGGER OneHeadUpdate
BEFORE UPDATE OF supervisor_id ON Employee
FOR EACH ROW
WHEN NEW.supervisor_id IS NULL
     AND EXISTS (SELECT * FROM Employee WHERE supervisor_id IS NULL AND employee_id <> NEW.employee_id)
BEGIN
    SELECT RAISE(ABORT, 'BR19: every employee must report to exactly one supervisor');
END;

CREATE TRIGGER NoSelfSupervision
BEFORE UPDATE OF supervisor_id ON Employee
FOR EACH ROW
WHEN NEW.supervisor_id IS NOT NULL
     AND EXISTS (WITH RECURSIVE chain(id) AS (
                     SELECT NEW.supervisor_id
                     UNION
                     SELECT e.supervisor_id FROM Employee e
                     JOIN chain c ON e.employee_id = c.id
                     WHERE e.supervisor_id IS NOT NULL)
                 SELECT * FROM chain WHERE id = NEW.employee_id)
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
    with closing(connect(db_name)) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.executescript(TRIGGERS_SQL)
        conn.commit()
    return db_name


def is_initialised(conn):
    return conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] > 0


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

DATA_SQL = """
-- INSERT statements go here.  Insert in foreign-key order, or the foreign keys
-- enforced by connect() will reject the rows:
--
--   PostalArea -> Member
--   ItemType   -> Item -> Copy
--   Employee (head librarian first: BR19 allows exactly one with no supervisor)
--   Room       -> Event -> Registration
--   Loan       -> Fine
--   WishlistItem
"""


def load_data(db_name=DB_NAME):
    """Run DATA_SQL.  A no-op while DATA_SQL holds nothing but comments."""
    with closing(connect(db_name)) as conn:
        conn.executescript(DATA_SQL)
        conn.commit()
    return db_name


if __name__ == "__main__":
    with closing(connect()) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.executescript(TRIGGERS_SQL)
        conn.executescript(DATA_SQL)
        conn.commit()
        counts = dict(conn.execute(
            "SELECT type, count(*) FROM sqlite_master "
            " WHERE type IN ('table', 'trigger') GROUP BY type"))
    print("%s ready: %d tables, %d triggers."
          % (DB_NAME, counts.get("table", 0), counts.get("trigger", 0)))
    print("Run `python user.py` to use the library.")
