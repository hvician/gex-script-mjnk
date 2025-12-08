import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import io
import re
from datetime import datetime
from matplotlib.widgets import Button
import matplotlib.widgets as widgets
import matplotlib.ticker as ticker
import tkinter as tk
from tkinter import ttk


from PyQt5.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton
)
from PyQt5.QtGui import QFont
import sys


default_font = ("DejaVu Sans", 20)  # Change to your preferred font and size


def parse_spx_quotedata(file_path, manual_price=None):
    """
    Parse the SPX quotedata CSV file and extract option data

    Parameters:
    file_path: Path to the SPX quotedata CSV file
    manual_price: Manually provided current price (optional)

    Returns:
    DataFrame with option data and current price
    """
    # Read the file content
    with open(file_path, 'r') as file:
        content = file.read()

    # If manual price is provided, use it
    current_price = None
    if manual_price is not None:
        current_price = float(manual_price)
    else:
        # Try to extract the current price from the first few lines
        first_few_lines = '\n'.join(content.split('\n')[:5])
        price_patterns = [
            r'Last: ([\d\.]+)',
            r'Last:[\s]*([\d\.]+)',
            r'S&P 500 INDEX.*?Last:[\s]*([\d\.]+)',
            r'SPX.*?Last:[\s]*([\d\.]+)',
            r'Last[\s]*Price:[\s]*([\d\.]+)',
            r'Price:[\s]*([\d\.]+)'
        ]

        for pattern in price_patterns:
            price_match = re.search(pattern, first_few_lines, re.IGNORECASE)
            if price_match:
                try:
                    current_price = float(price_match.group(1))
                    print(f"Found current price: {current_price}")
                    break
                except ValueError:
                    continue

    if current_price is None:
        # Prompt user for manual price input
        user_input = input(
            "Could not extract current price from file. Please enter the current SPX price: ")
        try:
            current_price = float(user_input)
        except ValueError:
            raise ValueError(
                "Invalid price entered. Please provide a valid number.")

    # Use pandas to read the CSV data
    try:
        # First, find the header row
        lines = content.split('\n')
        header_index = None

        # Look for a line that might be the header (contains 'Expiration Date' or 'Strike')
        for i, line in enumerate(lines):
            if 'Expiration Date' in line or ('Strike' in line and ('Calls' in line or 'Puts' in line)):
                header_index = i
                break

        if header_index is None:
            print(
                "Warning: Could not identify a header row, using the first row as header")
            header_index = 0

        # Read the CSV content starting from the header row
        csv_content = '\n'.join(lines[header_index:])
        df = pd.read_csv(io.StringIO(csv_content))

        # Print first few rows and columns for debugging
        print("\nFirst few columns:", df.columns[:10].tolist())
        print("\nFirst few rows:")
        print(df.head(2).to_string())

    except Exception as e:
        print(f"Error reading CSV data: {e}")
        raise

    # Process the DataFrame to extract option data
    option_data = []
    current_date = datetime.now().date()

    # Try to identify the correct column names
    expiry_col = find_column(df, ['Expiration Date', 'Expiry', 'ExpiryDate'])
    strike_col = find_column(df, ['Strike', 'Strike Price', 'StrikePrice'])
    call_oi_col = find_column(
        df, ['Open Interest', 'Call Open Interest', 'CallOI'])
    call_gamma_col = find_column(df, ['Gamma', 'Call Gamma', 'CallGamma'])
    put_oi_col = find_column(
        df, ['Open Interest.1', 'Put Open Interest', 'PutOI'])
    put_gamma_col = find_column(df, ['Gamma.1', 'Put Gamma', 'PutGamma'])

    # Try to find volume columns (if available)
    call_volume_col = find_column(
        df, ['Volume', 'Call Volume', 'CallVolume', 'Call Vol'])
    put_volume_col = find_column(
        df, ['Volume.1', 'Put Volume', 'PutVolume', 'Put Vol'])

    if not all([expiry_col, strike_col, call_oi_col, call_gamma_col, put_oi_col, put_gamma_col]):
        print("Warning: Some expected columns were not found.")

    # Process each row
    for index, row in df.iterrows():
        try:
            # Skip rows with missing data
            required_cols = [col for col in [
                strike_col, call_oi_col, call_gamma_col, put_oi_col, put_gamma_col] if col]
            if any(pd.isna(row.get(col)) for col in required_cols):
                continue

            # Parse strike price
            if strike_col:
                try:
                    strike = float(row[strike_col])
                except (ValueError, TypeError):
                    continue
            else:
                continue

            # Parse expiry date
            expiry_date_str = None
            T = 1/365.0
            expiry_date = None

            if expiry_col:
                expiry_date_str = str(row[expiry_col])
                if pd.notna(expiry_date_str):
                    try:
                        date_formats = ['%a %b %d %Y',
                                        '%Y-%m-%d', '%m/%d/%Y', '%d-%b-%Y']
                        for fmt in date_formats:
                            try:
                                expiry_date = datetime.strptime(
                                    expiry_date_str, fmt).date()
                                days_to_expiry = (
                                    expiry_date - current_date).days
                                T = max(days_to_expiry / 365.0, 0.001)
                                break
                            except ValueError:
                                continue
                    except:
                        T = 1/365.0

            call_oi = float(row[call_oi_col]) if call_oi_col and pd.notna(
                row[call_oi_col]) else 0
            call_gamma = float(row[call_gamma_col]) if call_gamma_col and pd.notna(
                row[call_gamma_col]) else 0
            put_oi = float(row[put_oi_col]) if put_oi_col and pd.notna(
                row[put_oi_col]) else 0
            put_gamma = float(row[put_gamma_col]) if put_gamma_col and pd.notna(
                row[put_gamma_col]) else 0

            # Get volume data if available, otherwise use open interest as proxy
            call_volume = float(row[call_volume_col]) if call_volume_col and pd.notna(
                row[call_volume_col]) else call_oi
            put_volume = float(row[put_volume_col]) if put_volume_col and pd.notna(
                row[put_volume_col]) else put_oi

            option_data.append({
                'Expiry': expiry_date_str if expiry_date_str else f"Row {index}",
                'ExpiryDate': expiry_date,
                'Month': expiry_date.strftime('%B %Y') if expiry_date else 'Unknown',
                'T': T,
                'Strike': strike,
                'Call_OI': call_oi,
                'Call_Gamma': call_gamma,
                'Put_OI': put_oi,
                'Put_Gamma': put_gamma,
                'Call_Volume': call_volume,
                'Put_Volume': put_volume
            })
        except Exception as e:
            print(f"Error processing row {index}: {e}")
            continue

    if not option_data:
        raise ValueError(
            "No valid option data could be extracted from the file")

    result_df = pd.DataFrame(option_data)
    print(f"Successfully processed {len(result_df)} option records")
    return result_df, current_price


def find_column(df, possible_names):
    """Find a column name from a list of possible names"""
    for name in possible_names:
        if name in df.columns:
            return name
    return None


def calculate_gamma_exposure(df, current_price):
    """Calculate gamma exposure from parsed option data"""
    result_df = df.copy()
    multiplier = 100

    for col in ['Call_Gamma', 'Call_OI', 'Put_Gamma', 'Put_OI', 'Call_Volume', 'Put_Volume']:
        result_df[col] = result_df[col].fillna(0)

    # This is the corrected calculation based on the prompt specifications
    result_df['Total_Call_Gamma'] = result_df['Call_Gamma'] * \
        result_df['Call_OI']
    result_df['Total_Put_Gamma'] = result_df['Put_Gamma'] * result_df['Put_OI']

    # Net Gamma at each strike = (Call gamma * OI) - (Put gamma * OI)
    result_df['Net_Gamma'] = result_df['Total_Call_Gamma'] - \
        result_df['Total_Put_Gamma']

    # Calculate gamma exposure (dollar values)
    result_df['Call_Gamma_Exposure'] = result_df['Total_Call_Gamma'] * \
        multiplier * current_price
    result_df['Put_Gamma_Exposure'] = result_df['Total_Put_Gamma'] * \
        multiplier * current_price
    result_df['Net_Gamma_Exposure'] = result_df['Call_Gamma_Exposure'] - \
        result_df['Put_Gamma_Exposure']

    # Calculate total volume by strike
    result_df['Total_Volume'] = result_df['Call_Volume'] + \
        result_df['Put_Volume']
    result_df['Total_OI'] = result_df['Call_OI'] + result_df['Put_OI']

    return result_df


def find_net_zero_gamma_level(df):
    """Find the strike level where NET gamma exposure crosses zero based on prompt specifications"""
    # Group by strike and calculate total net gamma for each strike
    strike_gamma = df.groupby('Strike').agg({
        'Net_Gamma': 'sum'
    }).reset_index()

    # Sort by strike to find the crossing point
    sorted_data = strike_gamma.sort_values('Strike').reset_index(drop=True)

    # Calculate cumulative gamma to find where total gamma changes sign
    sorted_data['Cumulative_Net_Gamma'] = sorted_data['Net_Gamma'].cumsum()

    for i in range(len(sorted_data) - 1):
        current = sorted_data.iloc[i]
        next_row = sorted_data.iloc[i + 1]

        # Check if cumulative net gamma crosses zero (positive to negative or negative to positive)
        if (current['Cumulative_Net_Gamma'] >= 0 and next_row['Cumulative_Net_Gamma'] < 0) or \
           (current['Cumulative_Net_Gamma'] <= 0 and next_row['Cumulative_Net_Gamma'] > 0):
            # Linear interpolation to find exact zero crossing
            current_cum_gamma = current['Cumulative_Net_Gamma']
            next_cum_gamma = next_row['Cumulative_Net_Gamma']
            current_strike = current['Strike']
            next_strike = next_row['Strike']

            if next_cum_gamma != current_cum_gamma:
                ratio = abs(current_cum_gamma) / \
                    abs(current_cum_gamma - next_cum_gamma)
                zero_gamma_strike = current_strike + \
                    ratio * (next_strike - current_strike)
                return zero_gamma_strike

    return None


def calculate_max_pain(df):
    """Calculate the max pain point based on prompt specifications"""
    # Get all unique strike values from the dataset
    strikes = sorted(df['Strike'].unique())
    pain_levels = []

    for test_price in strikes:
        # Calculate total pain for calls at this test price
        call_pain = 0
        put_pain = 0

        # For all calls, calculate intrinsic value if they're ITM
        for _, row in df.iterrows():
            strike = row['Strike']
            call_oi = row['Call_OI']
            put_oi = row['Put_OI']

            # Call intrinsic value
            call_intrinsic = max(0, test_price - strike)
            # Put intrinsic value
            put_intrinsic = max(0, strike - test_price)

            # Total pain is OI × intrinsic value (both calls and puts)
            call_pain += call_oi * call_intrinsic
            put_pain += put_oi * put_intrinsic

        total_pain = call_pain + put_pain
        pain_levels.append({'Strike': test_price, 'Total_Pain': total_pain})

    pain_df = pd.DataFrame(pain_levels)
    if not pain_df.empty:
        min_pain_idx = pain_df['Total_Pain'].idxmin()
        max_pain_strike = pain_df.loc[min_pain_idx, 'Strike']
        return max_pain_strike
    return None


def update_plot(ax_gamma, ax_volume, fig, df, filter_type, filter_value, current_price):
    """Update the plot with new data for the selected filter"""
    # Clear the current axes
    ax_gamma.clear()
    ax_volume.clear()

    # Filter the data based on selection
    if filter_type == "month":
        filtered_df = df[df['Month'] == filter_value]
        title_suffix = f" for {filter_value}"
    elif filter_type == "date":
        filtered_df = df[df['Expiry'] == filter_value]
        title_suffix = f" for {filter_value}"
    else:  # "all"
        filtered_df = df
        title_suffix = " for All Expiration Dates"

    if filtered_df.empty:
        print(f"No data available for {filter_value}")
        return

    # Group by strike price and sum the gamma exposure and volume
    strike_gamma = filtered_df.groupby('Strike').agg({
        'Net_Gamma_Exposure': 'sum',
        'Total_Volume': 'sum',
        'Total_OI': 'sum'
    }).reset_index()

    strike_gamma = strike_gamma.sort_values('Strike', ascending=False)

    # Create separate dataframes for positive and negative gamma values
    positive_df = strike_gamma[strike_gamma['Net_Gamma_Exposure'] >= 0]
    negative_df = strike_gamma[strike_gamma['Net_Gamma_Exposure'] < 0]

    # Set dark background for both axes
    ax_gamma.set_facecolor('#1A1A1A')
    ax_volume.set_facecolor('#1A1A1A')

    # GAMMA EXPOSURE PLOT (LEFT SIDE)
    # Find the maximum absolute value for symmetric x-axis
    max_abs_gamma = max(
        abs(strike_gamma['Net_Gamma_Exposure'].max()
            if not strike_gamma.empty else 0),
        abs(strike_gamma['Net_Gamma_Exposure'].min()
            if not strike_gamma.empty else 0)
    )
    x_limit_gamma = max_abs_gamma * 1.1

    # Create horizontal bar chart for gamma exposure
    if not positive_df.empty:
        ax_gamma.barh(positive_df['Strike'], positive_df['Net_Gamma_Exposure'],
                      color='#4ade80', alpha=0.7, height=100, label='Positive Gamma')
    if not negative_df.empty:
        ax_gamma.barh(negative_df['Strike'], negative_df['Net_Gamma_Exposure'],
                      color='#f87171', alpha=0.7, height=100, label='Negative Gamma')

    # Add lines and labels for gamma plot
    ax_gamma.axvline(x=0, color='white', linestyle='-', alpha=0.3)

    if current_price is not None:
        ax_gamma.axhline(y=current_price, color='#60a5fa', linestyle='--', linewidth=2,
                         label=f'Current Price: {current_price:.2f}')

    # Calculate Net Zero Gamma Level using the filtered dataset
    net_zero_gamma_level = find_net_zero_gamma_level(filtered_df)
    if net_zero_gamma_level is not None:
        ax_gamma.axhline(y=net_zero_gamma_level, color='#fbbf24', linestyle='--', linewidth=2,
                         label=f'Net Zero Gamma: {net_zero_gamma_level:.2f}')
        ax_gamma.text(x_limit_gamma * 0.8, net_zero_gamma_level, f'{net_zero_gamma_level:.2f}',
                      color='#fbbf24', ha='center', va='center', fontsize=20,
                      bbox=dict(boxstyle='round,pad=0.3', facecolor='black', alpha=0.7))

    # Calculate Max Pain using the filtered dataset
    max_pain = calculate_max_pain(filtered_df)
    if max_pain is not None:
        ax_gamma.axhline(y=max_pain, color='#ec4899', linestyle='--', linewidth=2,
                         label=f'Max Pain: {max_pain:.2f}')
        ax_gamma.text(x_limit_gamma * 0.7, max_pain, f'{max_pain:.2f}',
                      color='#ec4899', ha='center', va='center', fontsize=20,
                      bbox=dict(boxstyle='round,pad=0.3', facecolor='black', alpha=0.7))

    ax_gamma.set_xlim(-x_limit_gamma, x_limit_gamma)
    ax_gamma.set_xlabel('Net Gamma Exposure ($)', fontsize=20, color='white')
    ax_gamma.set_ylabel('Strike Price', fontsize=20, color='white')
    ax_gamma.set_title(
        'SPX Gamma Exposure{title_suffix}', fontsize=20, color='white', pad=20)

    # Major ticks every 125 units
    ax_gamma.yaxis.set_major_locator(ticker.MultipleLocator(125))

    # Enable grid lines
    ax_gamma.grid(True, which='major', linestyle='-',
                  linewidth=0.5, color='gray')

    # VOLUME/OPEN INTEREST PLOT (RIGHT SIDE)
    # Use Total_Volume if available, otherwise use Total_OI
    volume_data = strike_gamma['Total_Volume'] if strike_gamma['Total_Volume'].sum(
    ) > 0 else strike_gamma['Total_OI']
    volume_label = 'Total Options Volume' if strike_gamma['Total_Volume'].sum(
    ) > 0 else 'Total Open Interest'

    # Create horizontal bar chart for volume/OI
    ax_volume.barh(strike_gamma['Strike'], volume_data,
                   color='#60d5fa', alpha=0.7, height=100)

    # Add current price line to volume plot
    if current_price is not None:
        ax_volume.axhline(y=current_price, color='#60a5fa',
                          linestyle='--', linewidth=2)
        ax_volume.text(volume_data.max() * 0.95, current_price, f'{current_price:.2f}',
                       color='#60a5fa', ha='right', va='center', fontsize=20,
                       bbox=dict(boxstyle='round,pad=0.3', facecolor='black', alpha=0.7))

    # Add other key levels to volume plot
    if net_zero_gamma_level is not None:
        ax_volume.axhline(y=net_zero_gamma_level, color='#fbbf24',
                          linestyle='--', linewidth=2, alpha=0.5)

    if max_pain is not None:
        ax_volume.axhline(y=max_pain, color='#ec4899',
                          linestyle='--', linewidth=2, alpha=0.5)

    ax_volume.set_xlabel(volume_label, fontsize=20, color='white')
    ax_volume.set_ylabel('')  # Remove y-label for volume plot
    ax_volume.set_title(
        f'Options {volume_label}{title_suffix}', fontsize=20, color='white', pad=20)

    # Style both plots
    for ax in [ax_gamma, ax_volume]:
        ax.grid(True, alpha=0.2, color='white')
        for spine in ax.spines.values():
            spine.set_color('white')
            spine.set_alpha(0.5)
        ax.tick_params(colors='white', labelsize=20)

    # Add legend only to gamma plot
    legend = ax_gamma.legend(
        loc='upper right', framealpha=0.8, facecolor='black', edgecolor='white')
    for text in legend.get_texts():
        text.set_color('white')
        text.set_fontsize(20)

    # Add summary statistics
    total_gamma = strike_gamma['Net_Gamma_Exposure'].sum()
    total_volume_sum = volume_data.sum()

    fig.text(0.02, 0.02, f'Total Net Gamma Exposure: ${total_gamma/1e6:.2f}M | Total {volume_label}: {total_volume_sum/1e3:.1f}K',
             fontsize=20, bbox=dict(facecolor='black', alpha=0.8, edgecolor='white'),
             color='white')

    fig.canvas.draw()


def create_dropdown_window(df, current_price):
    app = QApplication.instance() or QApplication(sys.argv)

    # Create modal dialog
    dialog = QDialog()
    dialog.setWindowTitle("Select Date Filter")
    dialog.setStyleSheet("background-color: #2A2A2A; color: white;")
    dialog.setFixedSize(600, 150)

    # Set global font
    font = QFont("DejaVu Sans", 20)
    app.setFont(font)

    # Layouts
    layout = QVBoxLayout()
    row = QHBoxLayout()

    # Label
    label = QLabel("Select Date:")
    label.setFont(font)
    row.addWidget(label)

    # Dropdown
    available_dates = sorted(df['Expiry'].unique())
    combo = QComboBox()
    combo.setFont(font)
    combo.addItems(["All Dates"] + available_dates)
    combo.setStyleSheet("""
        QComboBox {
            background-color: #404040;
            color: white;
            padding: 5px;
        }
        QComboBox QAbstractItemView {
            background-color: #505050;
            color: white;
        }
    """)
    row.addWidget(combo)
    layout.addLayout(row)

    # Submit button
    button = QPushButton("Apply")
    button.setFont(QFont("Monospace", 20))
    button.setStyleSheet("background-color: #3A3A3A; color: white;")
    layout.addWidget(button)

    dialog.setLayout(layout)

    # Capture result
    selected_date = None

    def on_submit():
        nonlocal selected_date
        selected_date = combo.currentText()
        dialog.accept()

    button.clicked.connect(on_submit)
    dialog.exec_()

    return selected_date


def plot_gamma_exposure_interactive(df, current_price):
    """Create an interactive plot with Tkinter dropdown for date selection"""
    # Create the main Tk root for the application
    tk_root = tk.Tk()

    tk_root.withdraw()  # Hide the main window

    # First, show the dropdown to select date
    selected_date = create_dropdown_window(df, current_price)

    if selected_date is None:
        selected_date = "All Dates"

    # Set up the dark style
    plt.style.use('dark_background')

    # Create the figure with two subplots side by side
    fig, (ax_gamma, ax_volume) = plt.subplots(1, 2, figsize=(20, 20),
                                              gridspec_kw={'width_ratios': [1, 0.5]})
    fig.patch.set_facecolor('#1A1A1A')

    # Current filter state
    current_filter = {"type": "all" if selected_date == "All Dates" else "date",
                      "value": None if selected_date == "All Dates" else selected_date}

    # Update plot with selected date
    update_plot(ax_gamma, ax_volume, fig, df,
                current_filter["type"], current_filter["value"], current_price)

    # Create a change date button
    ax_button = plt.axes([0.85, 0.94, 0.12, 0.04])
    button = Button(ax_button, 'Change Date',
                    color='#3A3A3A', hovercolor='#4A4A4A')
    # Set larger font on the button label
    button.label.set_fontsize(20)  # Try 20 or 24 if needed
    button.label.set_fontname('DejaVu Sans')  # Optional: match your UI font

    def change_date(event):
        nonlocal current_filter
        new_date = create_dropdown_window(df, current_price)
        if new_date:
            current_filter["type"] = "all" if new_date == "All Dates" else "date"
            current_filter["value"] = None if new_date == "All Dates" else new_date
            update_plot(ax_gamma, ax_volume, fig, df,
                        current_filter["type"], current_filter["value"], current_price)
            fig.canvas.draw_idle()  # Force the canvas to redraw

    button.on_clicked(change_date)

    plt.tight_layout()
    plt.show()

    # Clean up Tk root
    tk_root.destroy()


def plot_gamma_exposure_by_expiry(df):
    """Plot gamma exposure by expiration date"""
    if df.empty:
        print("No data to plot")
        return

    expiry_gamma = df.groupby(
        'Expiry')['Net_Gamma_Exposure'].sum().reset_index()

    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(12, 8))

    fig.patch.set_facecolor('#1A1A1A')
    ax.set_facecolor('#1A1A1A')

    plt.bar(expiry_gamma['Expiry'], expiry_gamma['Net_Gamma_Exposure'],
            color='#4ade80', alpha=0.7, edgecolor='white')

    plt.axhline(y=0, color='white', linestyle='-', alpha=0.3)

    ax.grid(True, which="both", alpha=0.2, color='white')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['bottom'].set_color('white')
    ax.spines['left'].set_visible(False)

    plt.xlabel('Expiration Date', fontsize=20, color='white')
    plt.ylabel('Net Gamma Exposure ($)', fontsize=20, color='white')
    plt.title('Net Gamma Exposure by Expiration Date',
              fontsize=20, color='white', pad=20)
    plt.xticks(rotation=45, color='white', fontsize=20)

    ax.tick_params(colors='white')

    plt.tight_layout()
    plt.show()


def main():
    """Main function to process SPX quotedata and generate interactive gamma exposure plots"""
    try:
        # Get file path
        file_path = input("Enter the path to the SPX quotedata CSV file: ")

        # Ask if user wants to provide price manually
        manual_price_input = input(
            "Do you want to enter the current SPX price manually? (y/n): ")
        manual_price = None

        if manual_price_input.lower() == 'y':
            price_input = input("Enter the current SPX price: ")
            manual_price = float(price_input)

        print(f"Parsing file: {file_path}")
        option_data, current_price = parse_spx_quotedata(
            file_path, manual_price)

        if option_data.empty:
            print("No valid option data found in the file.")
            return

        print(f"\nCurrent SPX price: {current_price}")
        print(f"Number of option records: {len(option_data)}")

        print("\nCalculating gamma exposure...")
        result_df = calculate_gamma_exposure(option_data, current_price)

        print("\nSummary of Gamma Exposure:")
        print(f"Total Call Gamma: {result_df['Total_Call_Gamma'].sum():,.2f}")
        print(f"Total Put Gamma: {result_df['Total_Put_Gamma'].sum():,.2f}")
        print(f"Total Net Gamma: {result_df['Net_Gamma'].sum():,.2f}")
        print(
            f"Total Call Gamma Exposure: ${result_df['Call_Gamma_Exposure'].sum():,.2f}")
        print(
            f"Total Put Gamma Exposure: ${result_df['Put_Gamma_Exposure'].sum():,.2f}")
        print(
            f"Total Net Gamma Exposure: ${result_df['Net_Gamma_Exposure'].sum():,.2f}")
        print(
            f"Total Options Volume/OI: {result_df['Total_Volume'].sum():,.0f}")

        # Calculate overall Net Zero Gamma and Max Pain
        net_zero_gamma = find_net_zero_gamma_level(result_df)
        max_pain = calculate_max_pain(result_df)

        print(
            f"\nNet Zero Gamma Level: {net_zero_gamma:.2f}" if net_zero_gamma else "\nNet Zero Gamma Level: Not found")
        print(
            f"Max Pain Level: {max_pain:.2f}" if max_pain else "Max Pain Level: Not found")

        # Get available expiry dates
        available_dates = sorted(result_df['Expiry'].unique())
        print(f"\nFound {len(available_dates)} available expiration dates")
        print("First 5 dates:", available_dates[:5])
        print("Last 5 dates:", available_dates[-5:])

        print("\nOpening interactive chart with dropdown menu...")
        plot_gamma_exposure_interactive(result_df, current_price)

        # After interactive chart, ask if user wants to see expiry plot
        show_expiry_plot = input(
            "\nDo you want to see gamma exposure by expiration date? (y/n): ")
        if show_expiry_plot.lower() == 'y':
            print("Plotting gamma exposure by expiration date...")
            plot_gamma_exposure_by_expiry(result_df)

    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
