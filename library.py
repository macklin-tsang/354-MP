import sqlite3
from contextlib import closing

DB_NAME = "library.db"

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
    -- Any year from 0 to the present: the collection includes reproductions of
    -- antiquity, so a lower bound of 1000 would reject real holdings.  The upper
    -- bound stays because a publication date in the future is a typing slip, not
    -- a holding.  It is a literal because SQLite rejects date('now') and the
    -- other non-deterministic functions inside a CHECK.
    published_year integer
        CONSTRAINT publishedYearCheck
        CHECK (published_year IS NULL OR published_year BETWEEN 0 AND 2026),
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
    acquisition_method text  NOT NULL DEFAULT 'purchase'
        CONSTRAINT acquisitionMethodCheck CHECK (acquisition_method IN ('purchase', 'donation', 'transfer')),
    donated_by       integer,
    copy_status      text    NOT NULL DEFAULT 'available'
        CONSTRAINT copyStatusCheck CHECK (copy_status IN ('available', 'loaned', 'lost')),
    PRIMARY KEY (item_id, copy_number),
    CONSTRAINT donorValidityCheck CHECK (
        (acquisition_method =  'donation' AND donated_by IS NOT NULL) OR
        (acquisition_method <> 'donation' AND donated_by IS NULL)),
    FOREIGN KEY (item_id) REFERENCES Item(item_id) ON DELETE CASCADE,
    FOREIGN KEY (donated_by) REFERENCES Member(member_id) ON DELETE RESTRICT
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
    employee_id       integer NOT NULL,
    PRIMARY KEY (event_id),
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
    # Foreign keys are off for the rebuild only.  DROP TABLE deletes the rows
    # first and checks foreign keys as it goes, and Employee.supervisor_id
    # references Employee ON DELETE RESTRICT, so dropping a populated Employee
    # table trips its own constraint and no rebuild of a loaded database would
    # ever get past it.  They go back on before the connection is used again,
    # and load_data() below runs on a fresh connect() with them enforced.
    with closing(connect(db_name)) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript(SCHEMA_SQL)
        conn.executescript(TRIGGERS_SQL)
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
    return db_name


def is_initialised(conn):
    return conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] > 0


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

DATA_SQL = """
-- Seed data, inserted in foreign-key order, or the foreign keys enforced by
-- connect() will reject the rows.

-- ---------------------------------------------------------------------------
-- PostalArea
-- ---------------------------------------------------------------------------
INSERT INTO PostalArea (postal_code, city) VALUES
    ('V5A1S6', 'Burnaby'),
    ('V5A4X9', 'Burnaby'),
    ('V5B2K3', 'Burnaby'),
    ('V5C6P1', 'Burnaby'),
    ('V3N1R7', 'Burnaby'),
    ('V6B1H2', 'Vancouver'),
    ('V6E3M8', 'Vancouver'),
    ('V5K0A1', 'Vancouver'),
    ('V7A2L4', 'Richmond'),
    ('V6X3K7', 'Richmond'),
    ('V3L1H9', 'New Westminster'),
    ('V3J7C5', 'Coquitlam');

-- ---------------------------------------------------------------------------
-- Member
-- ---------------------------------------------------------------------------
INSERT INTO Member (member_id, first_name, last_name, email, phone, street,
                    postal_code, join_date, status) VALUES
    ( 1, 'Alice',    'Nguyen',    'alice.nguyen@example.com',    '604-555-0142', '3480 Kingsway',     'V5A1S6', '2023-01-14', 'active'),
    ( 2, 'Daniel',   'Okafor',    'daniel.okafor@example.com',   '604-555-0198', '1122 Halifax St',   'V5A4X9', '2023-03-02', 'active'),
    ( 3, 'Priya',    'Ramesh',    'priya.ramesh@example.com',    '778-555-0113', '4501 Hastings St',  'V5B2K3', '2023-06-21', 'active'),
    ( 4, 'Jonas',    'Lindqvist', 'jonas.lindqvist@example.com', '604-555-0167', '8080 Canada Way',   'V5C6P1', '2024-02-08', 'active'),
    ( 5, 'Mei Ling', 'Tan',       'meiling.tan@example.com',     '778-555-0124', '6920 Edmonds St',   'V3N1R7', '2024-05-30', 'active'),
    ( 6, 'Carlos',   'Mendes',    'carlos.mendes@example.com',   '604-555-0175', '350 Robson St',     'V6B1H2', '2024-07-19', 'suspended'),
    ( 7, 'Fatima',   'Al-Sayed',  'fatima.alsayed@example.com',  '778-555-0136', '1450 Davie St',     'V6E3M8', '2024-09-05', 'active'),
    ( 8, 'Grace',    'Kim',       'grace.kim@example.com',       '604-555-0189', '700 Renfrew St',    'V5K0A1', '2025-01-11', 'active'),
    ( 9, 'Tomasz',   'Kowalski',  'tomasz.kowalski@example.com', '778-555-0147', '6200 No 3 Rd',      'V7A2L4', '2025-02-27', 'active'),
    (10, 'Isabelle', 'Roy',       'isabelle.roy@example.com',    '604-555-0158', '8100 Ackroyd Rd',   'V6X3K7', '2025-04-16', 'active'),
    (11, 'Samuel',   'Boateng',   'samuel.boateng@example.com',  '778-555-0161', '620 Sixth St',      'V3L1H9', '2025-06-08', 'active'),
    (12, 'Hannah',   'Whitfield', 'hannah.whitfield@example.com','604-555-0172', '1188 Pinetree Way', 'V3J7C5', '2025-08-23', 'suspended'),
    (13, 'Rohan',    'Gupta',     'rohan.gupta@example.com',     '778-555-0183', '5120 Imperial St',  'V5A1S6', '2025-11-02', 'active'),
    (14, 'Elena',    'Petrova',   'elena.petrova@example.com',   '604-555-0194', '2255 Cambie St',    'V6B1H2', '2026-01-19', 'active');

-- ---------------------------------------------------------------------------
-- ItemType
-- Loan periods and fine rates differ by type; the OverdueCheck and FineAmount
-- triggers derive every due date and fine amount from these two columns.
-- ---------------------------------------------------------------------------
INSERT INTO ItemType (type_code, type_name, loan_period, daily_fine_rate) VALUES
    ('PB',  'print book',         21, 0.25),
    ('OB',  'online book',        14, 0.25),
    ('MAG', 'magazine',            7, 0.15),
    ('SJ',  'scientific journal',  7, 0.50),
    ('AR',  'audio record',       14, 0.30),
    ('AB',  'audiobook',          21, 0.20),
    ('DVD', 'dvd',                 7, 1.00),
    ('GN',  'graphic novel',      21, 0.25),
    ('REF', 'reference book',      3, 2.00),
    ('BG',  'board game',         14, 0.75);

-- ---------------------------------------------------------------------------
-- Item
-- ---------------------------------------------------------------------------
INSERT INTO Item (item_id, title, creator, published_year, language, type_code) VALUES
    ( 1, 'The Hobbit',                          'J. R. R. Tolkien',    1937, 'English',  'PB'),
    ( 2, 'Kitchen Confidential',                'Anthony Bourdain',    2000, 'English',  'PB'),
    ( 3, 'Braiding Sweetgrass',                 'Robin Wall Kimmerer', 2013, 'English',  'PB'),
    ( 4, 'The Left Hand of Darkness',           'Ursula K. Le Guin',   1969, 'English',  'PB'),
    ( 5, 'Klara and the Sun',                   'Kazuo Ishiguro',      2021, 'English',  'OB'),
    ( 6, 'National Geographic, October 2025',   NULL,                  2025, 'English',  'MAG'),
    ( 7, 'The Walrus, March 2026',              NULL,                  2026, 'English',  'MAG'),
    ( 8, 'Nature, Volume 630',                  NULL,                  2024, 'English',  'SJ'),
    ( 9, 'Journal of the ACM, Volume 71 No. 2', NULL,                  2024, 'English',  'SJ'),
    (10, 'Kind of Blue',                        'Miles Davis',         1959, NULL,       'AR'),
    (11, 'Rumours',                             'Fleetwood Mac',       1977, 'English',  'AR'),
    (12, 'Becoming',                            'Michelle Obama',      2018, 'English',  'AB'),
    (13, 'Spirited Away',                       'Hayao Miyazaki',      2001, 'Japanese', 'DVD'),
    (14, 'Persepolis',                          'Marjane Satrapi',     2000, 'English',  'GN'),
    (15, 'Oxford English Dictionary, 2nd ed.',  NULL,                  1989, 'English',  'REF'),
    (16, 'Wingspan',                            'Elizabeth Hargrave',  2019, 'English',  'BG');

-- ---------------------------------------------------------------------------
-- Copy
-- Every donation is dated after its donor's join_date.  Nothing in the schema
-- relates those two columns, so it is kept by hand.
-- ---------------------------------------------------------------------------
INSERT INTO Copy (item_id, copy_number, acquisition_date, acquisition_method,
                  donated_by, copy_status) VALUES
    ( 1, 1, '2019-03-12', 'purchase', NULL, 'available'),
    ( 1, 2, '2021-06-08', 'purchase', NULL, 'available'),
    ( 1, 3, '2024-11-02', 'donation',    1, 'available'),
    ( 2, 1, '2018-09-25', 'purchase', NULL, 'available'),
    ( 2, 2, '2022-01-17', 'purchase', NULL, 'available'),
    ( 3, 1, '2023-08-04', 'donation',    3, 'available'),
    ( 3, 2, '2024-02-20', 'purchase', NULL, 'available'),
    ( 4, 1, '2020-08-03', 'purchase', NULL, 'available'),
    ( 5, 1, '2022-10-11', 'purchase', NULL, 'available'),
    ( 6, 1, '2025-10-01', 'purchase', NULL, 'available'),
    ( 7, 1, '2026-03-02', 'purchase', NULL, 'available'),
    ( 8, 1, '2024-06-13', 'purchase', NULL, 'available'),
    ( 9, 1, '2024-04-05', 'transfer', NULL, 'available'),
    (10, 1, '2017-07-19', 'purchase', NULL, 'available'),
    (10, 2, '2025-03-15', 'donation',    9, 'available'),
    (11, 1, '2025-05-20', 'donation',   10, 'available'),
    (12, 1, '2021-02-22', 'purchase', NULL, 'available'),
    (13, 1, '2020-05-16', 'purchase', NULL, 'available'),
    (13, 2, '2025-03-08', 'donation',    5, 'available'),
    (14, 1, '2022-07-27', 'purchase', NULL, 'available'),
    (15, 1, '2016-01-20', 'transfer', NULL, 'available'),
    (16, 1, '2023-11-11', 'purchase', NULL, 'available'),
    (16, 2, '2025-11-20', 'donation',   13, 'available');

-- ---------------------------------------------------------------------------
-- Employee
-- ---------------------------------------------------------------------------
INSERT INTO Employee (employee_id, first_name, last_name, job_title, salary,
                      phone, supervisor_id) VALUES
    ( 1, 'Margaret', 'Chen',      'Head Librarian',          96500.00, '604-555-0201', NULL);
INSERT INTO Employee (employee_id, first_name, last_name, job_title, salary,
                      phone, supervisor_id) VALUES
    ( 2, 'David',    'Osei',      'Reference Librarian',     78200.00, '604-555-0202', 1),
    ( 3, 'Sofia',    'Marchetti', 'Children''s Librarian',   74800.00, '604-555-0203', 1),
    ( 4, 'Ahmed',    'Rahimi',    'Systems Librarian',       81300.00, '604-555-0204', 1),
    ( 5, 'Laura',    'Beckett',   'Circulation Supervisor',  66400.00, '604-555-0205', 1),
    ( 6, 'Kevin',    'Tran',      'Library Technician',      52900.00, '604-555-0206', 5),
    ( 7, 'Nadia',    'Haddad',    'Library Technician',      51500.00, '604-555-0207', 5),
    ( 8, 'Peter',    'Salmond',   'Events Coordinator',      61200.00, '604-555-0208', 1),
    ( 9, 'Yuki',     'Tanaka',    'Cataloguing Librarian',   72600.00, '604-555-0209', 4),
    (10, 'Marcus',   'Webb',      'Facilities Assistant',    47800.00, '604-555-0210', 5),
    (11, 'Chloe',    'Dubois',    'Teen Services Librarian', 70100.00, '604-555-0211', 2),
    (12, 'Ravi',     'Sharma',    'Shelving Assistant',      41200.00, '604-555-0212', 6);

-- Volunteers, written exactly as user.py's volunteer() writes them: job_title
-- 'Volunteer - <role>', salary 0, and the head librarian as supervisor, since
-- BR19 leaves her the only employee who may have none.
--
-- The names and phone numbers below are copied character for character from
-- members 3, 10 and 11.  Employee has no member_id, so that pair is the only
-- thing tying a volunteer back to the member who signed up, and it is what
-- volunteer() matches on to refuse a second sign-up.  Change a phone number in
-- Member without changing it here and the two rows silently stop referring to
-- the same person.
--
-- Kept to three on purpose: a salary of 0 is real payroll data, so every extra
-- volunteer drags an unfiltered AVG(salary) further down.  Queries about staff
-- want job_title NOT LIKE 'Volunteer%', which is what ask_librarian() does.
INSERT INTO Employee (employee_id, first_name, last_name, job_title, salary,
                      phone, supervisor_id) VALUES
    (13, 'Priya',    'Ramesh',    'Volunteer - Shelving',        0.00, '778-555-0113', 1),
    (14, 'Isabelle', 'Roy',       'Volunteer - Storytime',       0.00, '604-555-0158', 1),
    (15, 'Samuel',   'Boateng',   'Volunteer - Book Sale',       0.00, '778-555-0161', 1);

-- ---------------------------------------------------------------------------
-- Room
-- ---------------------------------------------------------------------------
INSERT INTO Room (room_number, capacity) VALUES
    (101,  12),
    (102,  20),
    (103,  30),
    (104,   8),
    (201,  45),
    (202,  60),
    (203,  15),
    (204,  25),
    (301, 100),
    (302,  40);

-- ---------------------------------------------------------------------------
-- Event
-- Events 1-5 have already happened; 6-13 are still to come.
-- ---------------------------------------------------------------------------
INSERT INTO Event (event_id, title, event_category, audience_category,
                   start_datetime, end_datetime, max_attendees, room_number,
                   employee_id) VALUES
    ( 1, 'Mystery Book Club: The Silent Patient', 'book club',      'adults',   '2026-06-11 18:30:00', '2026-06-11 20:00:00', 12, 101,  2),
    ( 2, 'Author Talk: Eden Robinson',            'author talk',    'adults',   '2026-06-18 19:00:00', '2026-06-18 20:30:00', 40, 201,  8),
    ( 3, 'Toddler Storytime',                     'workshop',       'children', '2026-07-08 10:00:00', '2026-07-08 11:00:00', 25, 103,  3),
    ( 4, 'Film Screening: Spirited Away',         'film screening', 'all ages', '2026-07-16 18:00:00', '2026-07-16 20:15:00', 35, 302,  8),
    ( 5, 'Watercolour Basics Workshop',           'workshop',       'seniors',  '2026-07-22 13:00:00', '2026-07-22 15:00:00', 20, 204,  6),
    ( 6, 'Local Artists Showcase',                'art show',       'all ages', '2026-08-14 17:00:00', '2026-08-14 20:00:00', 90, 301,  8),
    ( 7, 'Teen Graphic Novel Club',               'book club',      'teens',    '2026-08-19 16:00:00', '2026-08-19 17:30:00', 15, 203, 11),
    ( 8, 'Author Talk: Kazuo Ishiguro',           'author talk',    'adults',   '2026-08-27 19:00:00', '2026-08-27 20:30:00', 55, 202,  2),
    ( 9, 'Resume Writing Workshop',               'workshop',       'adults',   '2026-09-03 14:00:00', '2026-09-03 16:00:00', 18, 102,  5),
    (10, 'Seniors'' Tech Help Drop-in',           'workshop',       'seniors',  '2026-09-10 10:30:00', '2026-09-10 12:30:00',  8, 104,  4),
    (11, 'Indigenous Storytelling Evening',       'author talk',    'all ages', '2026-09-17 18:30:00', '2026-09-17 20:00:00', 42, 201,  9),
    (12, 'Board Game Night',                      'workshop',       'all ages', '2026-09-25 17:30:00', '2026-09-25 20:30:00', 28, 103,  6),
    (13, 'Film Screening: Persepolis',            'film screening', 'teens',    '2026-10-08 18:00:00', '2026-10-08 19:45:00', 30, 302, 11);

-- ---------------------------------------------------------------------------
-- Registration
-- ---------------------------------------------------------------------------
INSERT INTO Registration (member_id, event_id, registration_date, attended) VALUES
    ( 1,  1, '2026-05-28', 'present'),
    ( 3,  1, '2026-06-01', 'present'),
    ( 5,  1, '2026-06-03', 'absent'),
    ( 2,  2, '2026-06-02', 'present'),
    ( 4,  2, '2026-06-05', 'present'),
    ( 7,  2, '2026-06-09', 'absent'),
    (13,  2, '2026-06-10', 'present'),
    ( 8,  3, '2026-06-28', 'present'),
    (10,  3, '2026-07-01', 'present'),
    ( 9,  4, '2026-07-02', 'present'),
    (11,  4, '2026-07-05', 'absent'),
    (14,  4, '2026-07-09', 'present'),
    ( 5,  5, '2026-07-10', 'present'),
    (10,  5, '2026-07-14', 'present'),
    ( 1,  6, '2026-07-30', 'absent'),
    ( 2,  6, '2026-08-02', 'absent'),
    (13,  6, '2026-08-04', 'absent'),
    ( 8,  7, '2026-08-05', 'absent'),
    (14,  7, '2026-08-06', 'absent'),
    ( 3,  8, '2026-08-07', 'absent'),
    ( 7,  8, '2026-08-08', 'absent'),
    ( 9,  9, '2026-08-09', 'absent'),
    (11, 10, '2026-08-09', 'absent'),
    ( 4, 12, '2026-08-10', 'absent'),
    (13, 13, '2026-08-10', 'absent');

-- ---------------------------------------------------------------------------
-- Loan and Fine: the closed circulation history
--
-- Each loan is inserted with return_date NULL and then updated, never inserted
-- returned.  
-- ---------------------------------------------------------------------------

-- On time: due 2026-01-26.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (1, 1, 1, 1, '2026-01-05');
UPDATE Loan SET return_date = '2026-01-20' WHERE loan_id = 1;

-- 12 days late: due 2026-01-29, print book at 0.25/day.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (2, 2, 1, 2, '2026-01-08');
UPDATE Loan SET return_date = '2026-02-10' WHERE loan_id = 2;
INSERT INTO Fine (loan_id, assessed_date, fine_status) VALUES (2, '2026-02-10', 'paid');

-- Returned on the due date itself, so not overdue: due 2026-01-19.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (3, 6, 1, 3, '2026-01-12');
UPDATE Loan SET return_date = '2026-01-19' WHERE loan_id = 3;

-- 8 days late: due 2026-01-22, journal at 0.50/day.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (4, 8, 1, 4, '2026-01-15');
UPDATE Loan SET return_date = '2026-01-30' WHERE loan_id = 4;
INSERT INTO Fine (loan_id, assessed_date, fine_status) VALUES (4, '2026-01-30', 'paid');

-- On time: due 2026-02-16.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (5, 10, 1, 5, '2026-02-02');
UPDATE Loan SET return_date = '2026-02-14' WHERE loan_id = 5;

-- 8 days late: due 2026-02-12, dvd at 1.00/day.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (6, 13, 1, 7, '2026-02-05');
UPDATE Loan SET return_date = '2026-02-20' WHERE loan_id = 6;
INSERT INTO Fine (loan_id, assessed_date, fine_status) VALUES (6, '2026-02-20', 'paid');

-- 2 days late: due 2026-03-04, graphic novel at 0.25/day.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (7, 14, 1, 8, '2026-02-11');
UPDATE Loan SET return_date = '2026-03-06' WHERE loan_id = 7;
INSERT INTO Fine (loan_id, assessed_date, fine_status) VALUES (7, '2026-03-06', 'paid');

-- On time: due 2026-03-11.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (8, 3, 1, 9, '2026-02-18');
UPDATE Loan SET return_date = '2026-03-11' WHERE loan_id = 8;

-- 5 days late: due 2026-03-04, reference book at 2.00/day.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (9, 15, 1, 10, '2026-03-01');
UPDATE Loan SET return_date = '2026-03-09' WHERE loan_id = 9;
INSERT INTO Fine (loan_id, assessed_date, fine_status) VALUES (9, '2026-03-09', 'outstanding');

-- 8 days late: due 2026-03-17, board game at 0.75/day.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (10, 16, 1, 11, '2026-03-03');
UPDATE Loan SET return_date = '2026-03-25' WHERE loan_id = 10;
INSERT INTO Fine (loan_id, assessed_date, fine_status) VALUES (10, '2026-03-25', 'paid');

-- On time: due 2026-03-31.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (11, 4, 1, 13, '2026-03-10');
UPDATE Loan SET return_date = '2026-03-28' WHERE loan_id = 11;

-- 8 days late: due 2026-04-04, audiobook at 0.20/day.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (12, 12, 1, 14, '2026-03-14');
UPDATE Loan SET return_date = '2026-04-12' WHERE loan_id = 12;
INSERT INTO Fine (loan_id, assessed_date, fine_status) VALUES (12, '2026-04-12', 'paid');

-- 5 days late: due 2026-03-27, journal at 0.50/day.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (13, 9, 1, 1, '2026-03-20');
UPDATE Loan SET return_date = '2026-04-01' WHERE loan_id = 13;
INSERT INTO Fine (loan_id, assessed_date, fine_status) VALUES (13, '2026-04-01', 'outstanding');

-- On time: due 2026-04-23.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (14, 1, 2, 2, '2026-04-02');
UPDATE Loan SET return_date = '2026-04-23' WHERE loan_id = 14;

-- 6 days late: due 2026-04-19, audio record at 0.30/day.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (15, 11, 1, 3, '2026-04-05');
UPDATE Loan SET return_date = '2026-04-25' WHERE loan_id = 15;
INSERT INTO Fine (loan_id, assessed_date, fine_status) VALUES (15, '2026-04-25', 'paid');

-- On time: due 2026-04-23.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (16, 5, 1, 4, '2026-04-09');
UPDATE Loan SET return_date = '2026-04-21' WHERE loan_id = 16;

-- 6 days late: due 2026-04-22, magazine at 0.15/day.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (17, 7, 1, 5, '2026-04-15');
UPDATE Loan SET return_date = '2026-04-28' WHERE loan_id = 17;
INSERT INTO Fine (loan_id, assessed_date, fine_status) VALUES (17, '2026-04-28', 'outstanding');

-- 7 days late: due 2026-05-23, print book at 0.25/day.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (18, 2, 2, 7, '2026-05-02');
UPDATE Loan SET return_date = '2026-05-30' WHERE loan_id = 18;
INSERT INTO Fine (loan_id, assessed_date, fine_status) VALUES (18, '2026-05-30', 'paid');

-- 5 days late: due 2026-05-13, dvd at 1.00/day.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (19, 13, 2, 8, '2026-05-06');
UPDATE Loan SET return_date = '2026-05-18' WHERE loan_id = 19;
INSERT INTO Fine (loan_id, assessed_date, fine_status) VALUES (19, '2026-05-18', 'outstanding');

-- On time: due 2026-05-26.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (20, 16, 2, 9, '2026-05-12');
UPDATE Loan SET return_date = '2026-05-26' WHERE loan_id = 20;

-- 7 days late: due 2026-06-15, audio record at 0.30/day.
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES (21, 10, 2, 10, '2026-06-01');
UPDATE Loan SET return_date = '2026-06-22' WHERE loan_id = 21;
INSERT INTO Fine (loan_id, assessed_date, fine_status) VALUES (21, '2026-06-22', 'paid');

-- ---------------------------------------------------------------------------
-- Overdue Loans
-- ---------------------------------------------------------------------------
INSERT INTO Loan (loan_id, item_id, copy_number, member_id, checkout_date) VALUES
    (22,  1, 3,  1, '2026-07-28'),
    (23,  3, 2,  5, '2026-08-01'),
    (24,  6, 1,  8, '2026-08-03'),
    (25, 12, 1, 11, '2026-07-20'),
    (26, 14, 1, 13, '2026-08-05'),
    (27,  4, 1, 14, '2026-08-07'),
    (28,  8, 1,  3, '2026-06-30'),
    (29, 15, 1,  9, '2026-08-08');

-- ---------------------------------------------------------------------------
-- WishlistItem
-- Requests still pending have no reviewer yet; request 13 came from a staff
-- member at the desk rather than a member, so requested_by is NULL.
-- ---------------------------------------------------------------------------
INSERT INTO WishlistItem (request_id, proposed_title, proposed_creator,
                          proposed_format, cost, requested_date, status,
                          requested_by, reviewed_by) VALUES
    ( 1, 'The Ministry for the Future',           'Kim Stanley Robinson', 'print book',           24.99, '2026-03-04', 'approved',   1,    9),
    ( 2, 'Tomorrow, and Tomorrow, and Tomorrow',  'Gabrielle Zevin',      'print book',           27.50, '2026-03-19', 'acquired',   3,    9),
    ( 3, 'Nature Reviews Physics, 2026',          NULL,                   'scientific journal', 1450.00, '2026-04-02', 'pending',    4, NULL),
    ( 4, 'Cook''s Illustrated, annual',           NULL,                   'magazine',             39.95, '2026-04-11', 'approved',   5,    2),
    ( 5, 'Blue Train',                            'John Coltrane',        'audio record',         32.00, '2026-04-20', 'rejected',   7,    2),
    ( 6, 'Project Hail Mary',                     'Andy Weir',            'online book',          18.99, '2026-05-06', 'acquired',   8,    4),
    ( 7, 'The Hundred Years'' War on Palestine',  'Rashid Khalidi',       'print book',           26.00, '2026-05-15', 'pending',    9, NULL),
    ( 8, 'ACM Transactions on Database Systems',  NULL,                   'scientific journal',  890.00, '2026-05-27', 'approved',  10,    4),
    ( 9, 'Maus',                                  'Art Spiegelman',       'print book',           22.95, '2026-06-08', 'acquired',  11,    9),
    (10, 'The Guardian Weekly',                   NULL,                   'magazine',            210.00, '2026-06-19', 'rejected',  13,    2),
    (11, 'Song of Solomon',                       'Toni Morrison',        'online book',          16.99, '2026-07-01', 'pending',   14, NULL),
    (12, 'Rumours, 40th Anniversary Edition',     'Fleetwood Mac',        'audio record',         45.00, '2026-07-14', 'pending',    2, NULL),
    (13, 'Cosmos',                                'Carl Sagan',           'print book',            NULL, '2026-07-25', 'pending', NULL, NULL);
"""

def load_data(db_name=DB_NAME):
    with closing(connect(db_name)) as conn:
        conn.executescript(DATA_SQL)
        conn.commit()
    return db_name

if __name__ == "__main__":
    init_db()
    load_data()
    print(f"{DB_NAME} ready. Run `python user.py` to use the library.")
