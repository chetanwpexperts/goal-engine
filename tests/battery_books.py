"""Workbooks from different fields, with random data, for testing questions on files the agent has never seen."""

import datetime
import random

from openpyxl import Workbook

D0 = datetime.date(2026, 1, 1)


def make_all(folder, seed=11):
    rnd = random.Random(seed)
    F = ["Aditi", "Pooja", "Neha", "Simran", "Kavya", "Riya", "Anjali", "Sneha", "Isha", "Meera", "Tanvi", "Priya"]
    M = ["Arjun", "Rohan", "Rahul", "Vikram", "Aman", "Karan", "Dev", "Ishaan", "Manav", "Yash", "Aarav", "Kabir"]
    U = ["Kiran", "Harpreet", "Gurpreet"]
    last = ["Sharma", "Verma", "Gill", "Khan", "Singh", "Mehta", "Rana", "Joshi"]
    books = {}

    def save(name, sheets):
        wb = Workbook()
        wb.remove(wb.active)
        for title, header, rows in sheets:
            ws = wb.create_sheet(title)
            ws.append(header)
            for r in rows:
                ws.append(r)
        path = f"{folder}/{name}.xlsx"
        wb.save(path)
        books[name] = (path, sheets)

    # school with a Gender column (M/F) and date of birth
    rows = []
    for i in range(1, 91):
        g = rnd.choice("MF")
        rows.append([i, f"{rnd.choice(F if g == 'F' else M)} {rnd.choice(last)}", g, rnd.choice(["6", "7", "8"]), rnd.choice("AB"),
                     datetime.date(rnd.randint(2010, 2014), rnd.randint(1, 12), rnd.randint(1, 28)), rnd.randint(25, 100), rnd.randint(25, 100),
                     rnd.choice(["Yes", "No"]), rnd.choice(["Mohali", "Kharar", "Zirakpur"])])
    save("school", [("Students", ["Roll No", "Student Name", "Gender", "Class", "Section", "DOB", "Maths", "Science", "Fee Paid", "City"], rows)])
    # office HR: no gender column, full names, joining date, email, mobile
    rows = []
    for i in range(1, 61):
        fn = rnd.choice(F + M + U)
        rows.append([f"E{i:03}", f"{fn} {rnd.choice(last)}", rnd.choice(["Sales", "Accounts", "HR", "IT"]), rnd.choice(["Executive", "Manager", "Intern"]),
                     datetime.date(rnd.randint(2016, 2025), rnd.randint(1, 12), rnd.randint(1, 28)), rnd.randint(15, 90) * 1000,
                     f"{fn.lower()}{i}@" + rnd.choice(["gmail.com", "yahoo.com", "acme.in"]), f"98{rnd.randint(10000000, 99999999)}", rnd.choice(["Active", "Left"])])
    save("hr", [("Staff", ["Emp Code", "Name", "Department", "Designation", "Date of Joining", "Salary", "Email", "Mobile", "Status"], rows)])
    # shop with cost and selling price
    rows = []
    for i in range(1, 121):
        cp = rnd.choice([80, 150, 400, 900])
        rows.append([f"B{i}", D0 + datetime.timedelta(days=rnd.randint(0, 270)), rnd.choice(["Ramesh", "Gupta Store", "Walk-in", "Jain Traders"]),
                     rnd.choice(["Soap", "Rice 5kg", "Oil 1L", "Tea"]), rnd.randint(1, 10), cp, int(cp * rnd.choice([1.1, 1.2, 1.3])),
                     rnd.choice(["Cash", "UPI", "Card"]), rnd.choice([5, 12, 18])])
    save("shop", [("Bills", ["Bill No", "Date", "Customer", "Item", "Qty", "Cost Price", "Selling Price", "Payment Mode", "GST %"], rows)])
    # clinic with Sex as Male/Female and an Age column
    rows = []
    for i in range(1, 81):
        g = rnd.choice(["Male", "Female"])
        rows.append([f"P{i}", f"{rnd.choice(F if g == 'Female' else M)} {rnd.choice(last)}", g, rnd.randint(5, 85), rnd.choice(["Dr Rao", "Dr Mehta", "Dr Kaur"]),
                     D0 + datetime.timedelta(days=rnd.randint(0, 200)), rnd.choice([500, 1200, 3000, 15000]), rnd.choice(["Paid", "Pending"])])
    save("clinic", [("Patients", ["Patient ID", "Patient Name", "Sex", "Age", "Doctor", "Visit Date", "Bill Amount", "Payment"], rows)])
    # hotel bookings, two sheets
    rows = []
    for i in range(1, 71):
        ci = D0 + datetime.timedelta(days=rnd.randint(0, 250))
        n = rnd.randint(1, 5)
        rows.append([f"BK{i}", f"{rnd.choice(F + M)} {rnd.choice(last)}", ci, ci + datetime.timedelta(days=n), rnd.choice(["Deluxe", "Suite", "Standard"]), n,
                     n * rnd.choice([2500, 4000, 7000]), rnd.choice(["Website", "Walk-in", "Agent"]), rnd.randint(1, 5)])
    rooms = [["Deluxe", 20, 4000], ["Suite", 5, 7000], ["Standard", 30, 2500]]
    save("hotel", [("Bookings", ["Booking ID", "Guest Name", "Check In", "Check Out", "Room Type", "Nights", "Amount", "Source", "Rating"], rows),
                   ("Rooms", ["Room Type", "Rooms", "Rate"], rooms)])
    return books
