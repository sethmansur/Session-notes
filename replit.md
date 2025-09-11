# Speech Therapy Data Collection App

## Overview

A lightweight, iPad-friendly web application designed for speech therapists to collect and track therapy session data. The app manages students, their IEP objectives, and session progress with a simple interface optimized for touch interactions. Built with Flask, SQLite, and vanilla JavaScript to ensure fast performance and minimal dependencies.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Frontend Architecture
- **Template Engine**: Jinja2 templates with Flask for server-side rendering
- **Styling**: Custom CSS with mobile-first responsive design, optimized for iPad usage
- **JavaScript**: Vanilla JavaScript for client-side interactions, avoiding heavy frameworks
- **Navigation**: Fixed bottom navigation bar for easy thumb access on tablets
- **UI Components**: Card-based layout with large touch targets and clear visual hierarchy

### Backend Architecture
- **Web Framework**: Flask with minimal configuration for lightweight operation
- **Database Layer**: Direct SQLite3 integration using Python's built-in sqlite3 module
- **API Design**: RESTful endpoints with JSON responses for AJAX operations
- **Session Management**: Stateless design with date-based session tracking
- **Data Validation**: Server-side validation with graceful error handling

### Database Design
- **Students Table**: Stores student information with unique first names
- **Objectives Table**: Links therapy objectives to specific students via foreign keys
- **Sessions Table**: Date-based session tracking with unique date constraints
- **Events Table**: Core data collection table linking students, objectives, and session counts
- **Referential Integrity**: Foreign key constraints with cascade deletes for data consistency
- **Indexing**: Optimized queries with indices on frequently accessed foreign keys

### Core Features Architecture
1. **Student & Objective Management**: Bulk import capability with automatic text parsing for numbered lists
2. **Data Collection Interface**: Multi-student selection with real-time counter updates and session persistence
3. **Reporting System**: Flexible date range filtering with CSV export functionality for spreadsheet integration

## External Dependencies

### Runtime Dependencies
- **Flask**: Lightweight WSGI web application framework
- **SQLite3**: Built-in Python database module (no external database server required)

### Development Environment
- **Python 3**: Core runtime environment
- **No CSS Frameworks**: Custom styling to minimize load times and maintain simplicity
- **No JavaScript Frameworks**: Vanilla JS for maximum performance on mobile devices

### Export Integration
- **CSV Module**: Built-in Python CSV handling for spreadsheet compatibility
- **Browser APIs**: Uses standard HTML5 features for file downloads and date picking

### Device Optimization
- **iPad Safari**: Primary target browser with touch-optimized interface
- **Responsive Design**: CSS media queries for various screen sizes
- **Performance**: Minimal asset loading and efficient database queries for smooth operation