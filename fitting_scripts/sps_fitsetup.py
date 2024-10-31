'''
This is the script containing the functions to set up properly the data for the spelfig fitting
methods.
'''
import os
import random
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks, peak_widths
from scipy.optimize import curve_fit

import spl_models as spm
import spl_config as spc

## EMISSION LINE PRELIMINARY ANALYSIS: =============================================================
## This part contains the functions

def analyze_emission_lines(x, y, lines_dict, window=20.):
    '''
    Identify emission lines in a given observed spectrum and return the results.
    :param spectrum:
    :param lines_dict:
    :param window:
    :return:
    '''
    observed_wavelengths = x
    flux = y

    matched_lines = []
    results = []
    synthetic_flux = np.copy(flux)  # This maintains the original 1D flux array structure

    # Ensure the synthetic_flux initialization doesn't start with NaN values
    if np.isnan(synthetic_flux).all():
        synthetic_flux[:] = 0  # Set to zero or some baseline if entirely NaN

    continuum_mask = np.ones(len(flux), dtype=bool)
    for name, params in lines_dict.items():
        rest_wavelength = params['wavelength'][0]
        if rest_wavelength is not None:
            lower_bound = rest_wavelength - window
            upper_bound = rest_wavelength + window
            mask = (observed_wavelengths >= lower_bound) & (observed_wavelengths <= upper_bound)
            window_flux = flux[mask]
            window_wavelengths = observed_wavelengths[mask]

            # Verify the window selection:
            # if window_wavelengths.size:
                # No other option but plotting. What the heck is happening here?
                # plt.plot(window_wavelengths, window_flux)
                # plt.show()
                # print('len window_wavelengths', len(window_wavelengths))
                # print('This is the maximum flux inside the window', np.nanmax(window_flux))

            if not window_wavelengths.size:
                continue

            peaks, _ = find_peaks(window_flux, prominence=0.5)
            closest_peak_idx = None
            min_diff = float('inf')

            for peak_idx in peaks:
                ## This lines were meant to identify the closest peak, but they might screw up
                # the things if the redshift of the spectrum is not quite correct.

                observed_wavelength = window_wavelengths[peak_idx]
                diff = abs(observed_wavelength - rest_wavelength)

                if diff < min_diff:
                    min_diff = diff
                    closest_peak_idx = peak_idx

            if closest_peak_idx is not None:
                peak_flux = window_flux[closest_peak_idx]
                observed_wavelength = window_wavelengths[closest_peak_idx]
                matched_lines.append({
                    'line': name,
                    'restframe_wavelength': rest_wavelength,
                    'observed_wavelength': observed_wavelength,
                    'peak_flux': peak_flux,
                    'peak_idx': np.where(observed_wavelengths == observed_wavelength)[0][0]
                })


    # Update continuum mask and calculate synthetic data for gaps
    for line in matched_lines:
        peak_idx = line['peak_idx']
        widths, width_heights, left_ips, right_ips = peak_widths(flux, [peak_idx], rel_height=0.5)
        fwhm = np.interp(left_ips + widths, np.arange(len(flux)), observed_wavelengths) - \
               np.interp(left_ips, np.arange(len(flux)), observed_wavelengths)

        sigma = fwhm[0] / 2.355
        line_start = line['observed_wavelength'] - fwhm[0] / 2 - 3 * sigma
        line_end = line['observed_wavelength'] + fwhm[0] / 2 + 3 * sigma

        results.append({
            'line': line['line'],
            'restframe_wavelength': line['restframe_wavelength'],
            'observed_wavelength': line['observed_wavelength'],
            'fwhm': fwhm[0],
            'peak_flux': line['peak_flux']
        })

        mask = ((observed_wavelengths >= line_start) & (observed_wavelengths <= line_end))
        continuum_mask &= ~mask
        if np.any(mask):
            valid_flux = flux[~mask]
            if valid_flux.size > 0 and not np.isnan(valid_flux).all():
                mean_flux = np.nanmean(valid_flux)
                std_flux = np.nanstd(valid_flux)
                synthetic_flux[mask] = np.random.normal(0.0, 0.1 * std_flux, np.sum(mask))
            else:
                synthetic_flux[mask] = 0  # Fallback if no valid data is available

    # Apply the mask to keep only continuum points
    continuum_spectrum = np.column_stack((observed_wavelengths, synthetic_flux))
    std_cont = np.nanstd(continuum_spectrum[:,1])
    return results, std_cont, continuum_spectrum


def filter_and_prepare_linelist(line_results, continuum_spec0, wavelength_range, snr_ext,
                                window_width=10.):
    min_wavelength, max_wavelength = wavelength_range
    filtered_linelist = []
    # If noise standard deviation is not provided, assume a default or calculate externally

    three_sigma = 3 * snr_ext  # 3 sigma threshold for noise

    for line in line_results:
        name = line['line']
        line_wavelength = line['observed_wavelength']
        line_fwhm = line['fwhm']
        line_flux = line['peak_flux']
        sigma_max = line_fwhm / (2.0 * np.sqrt(2.0 * np.log(2.0)))  # Convert FWHM to sigma

        # Filter based on the wavelength range and FWHM constraints
        if min_wavelength <= line_wavelength <= max_wavelength:
            lineloc_min = line_wavelength - 2 * sigma_max
            lineloc_max = line_wavelength + 2 * sigma_max

            window_left = continuum_spec0[(continuum_spec0[:, 0] <= lineloc_min) &
                                          (continuum_spec0[:, 0] >= lineloc_min - window_width)]
            window_right = continuum_spec0[(continuum_spec0[:, 0] <= lineloc_max + window_width) &
                                           (continuum_spec0[:, 0] >= lineloc_max)]

            local_std = np.mean([np.std(window_left[:, 1]), np.std(window_right[:, 1])])
            local_mean = np.mean([np.mean(window_left[:, 1]), np.mean(window_right[:, 1])])

            # Evaluate inf snr in the line region is good regardless of entire spectrum
            snr = (line_flux - local_mean) / local_std if local_std > 0 else snr_ext

            # Check if the SNR is above the threshold and the peak flux is significant above 3 sigma
            if snr >= snr_ext or line_flux > three_sigma:
                line_details = {
                    'name': name,
                    'wavelength': line_wavelength,
                    'sigma': sigma_max,
                    'min_loc': lineloc_min,
                    'max_loc': lineloc_max,
                    'min_sd': 2.0,
                    'max_sd': 1.5*sigma_max,
                    'max_flux': line_flux,
                    'SNR': snr,  # Including SNR in the output for reference
                }
                filtered_linelist.append(line_details)
    return filtered_linelist


def continuum_init(continuum_spec, g_init):
    '''
    Initial guess for the parameters of the continuum. It uses a first approach fit with
    :param continuum_spec:
    :return:
    '''
    x_continuum = continuum_spec[:, 0]
    y_continuum = continuum_spec[:, 1]
    a_init = np.mean(y_continuum)
    loc0_init = np.min(x_continuum)
    p0 = [a_init, loc0_init, g_init]
    params, params_covariance = curve_fit(spm.continuum_function, x_continuum, y_continuum, p0=p0)

    return params


def initial_dataframe(emlines_dict, filtered_linelist, continuum_pars=None):
    '''
    This function creates an initial parameters dataframe given the emission lines dictionary.
    :param emlines_dict: The dictionary of emission lines
    :return:
    '''
    # Initialize lists to hold data for DataFrame
    line_names = []
    models = []
    ncomp = []
    parameters = []
    min_limits = []
    max_limits = []

    emlines = emlines_dict.keys()
    for line in filtered_linelist:
        line_name = line['name']
        components = [emlines_dict[line_name]['components']]
        Ncomp = len(components)
        for j, component in enumerate(components):
            if component == 'Voigt':
                params_i = [line['wavelength'], line['max_flux']/Ncomp, line['sigma'],
                            1.11*line['sigma']]
                max_i = [line['max_loc'], line['max_flux'], line['max_sd'], 1.11*line['max_sd']]
                min_i = [line['min_loc'], 0., line['min_sd'], 1.11*line['min_sd']]
            elif component == 'Gaussian':
                params_i = [line['wavelength'], line['max_flux']/Ncomp, line['sigma']]
                max_i = [line['max_loc'], line['max_flux'], line['max_sd']]
                min_i = [line['min_loc'], 0., line['min_sd']]
            elif component == 'Lorentzian':
                params_i = [line['wavelength'], line['max_flux']/Ncomp, 1.11*line['sigma']]
                max_i = [line['max_loc'], line['max_flux'], 1.11*line['max_sd']]
                min_i = [line['min_loc'], 0., 1.11*line['min_sd']]

            line_names.append(line_name)
            models.append(component)  # Assuming wavelength as centroid
            ncomp.append(j+1)  # Using max_flux as initial guess for amplitude
            parameters.append(params_i)
            min_limits.append(min_i)
            max_limits.append(max_i)

    dfparams = pd.DataFrame({
        'Line Name': line_names,
        'Model': models,  # Initial components set to 1
        'Component': ncomp,
        'Parameters': parameters,
        'Max Limits': max_limits,  # Using max_flux as initial guess for amplitude': sigmas,
        'Min Limits': min_limits,
    })

    if continuum_pars is not None:
        dfparams_cont = pd.DataFrame({
            'Line Name': ['Continuum'],
            'Model': ['Continuum'],  # Initial components set to 1
            'Component': [0.0],
            'Parameters': [continuum_pars],
            'Max Limits': [[np.inf, np.inf, np.inf]],
            'Min Limits': [[0, 0, 0]]
        })

        dfparams = pd.concat([dfparams, dfparams_cont], ignore_index=True)

    return dfparams


def init_setup(spectrum, emlines_dict, wavelength_range, gamma_init):
    '''
    This function sets up all the objects necessary for the execution of the fit functions
    :param spectrum:
    :param lines_dict:
    :param gamma_init: Initial guess for
    '''

    spectrum = spectrum[(spectrum[:,0]>=wavelength_range[0]) & (spectrum[:,0]<=wavelength_range[1])]
    x = spectrum[:, 0]
    y = spectrum[:, 1]
    dy = spectrum[:, 2]
    
    # First restrict the spectrum to the wavelength range:

    # Find the lines present in the spectrum and estimate first guesses for parameters:
    lines_init, snr_cont, continuum0 = analyze_emission_lines(x, y, emlines_dict)

    # Initial guess for the parameters of the continuum:
    continuum_pars = continuum_init(continuum0, gamma_init)

    # Filter the list of lines present in the spectrum:
    linelist0 = filter_and_prepare_linelist(lines_init, continuum0, wavelength_range, snr_cont)

    # Create an initial parameters dataframe:
    dfparams = initial_dataframe(emlines_dict, linelist0, continuum_pars=continuum_pars)

    return dfparams

# MULTICOMPONENT: ============================================================
# Functions to increase the number of components in the model:

def minmaxlim(df):
  min_limits = []
  max_limits = []

  for index, row in df.iterrows():

      # Definitions
      # standard deviation
      sigma = row['Parameters'][2]
      minsig = 2.0
      maxsig = 1.5 * sigma
      # wavelength
      line_wavelength = row['Parameters'][0]
      min_line = line_wavelength - 2 * sigma
      max_line = line_wavelength + 2 * sigma
      # amplitude
      amplitude = row['Parameters'][1]
      # components
      ncomp = row['Component']

      # Adjust maximum amplitude based on component number
      # for the first component (which can have more than one or just one component afterwards)
      if ncomp == 1:
          # if there are multiple components for this line
          if df[df['Line Name'] == row['Line Name']].shape[0] > 1:
              amplitude_factor = 2
          else:
              amplitude_factor = 1
      # for the second component
      elif ncomp == 2:
          amplitude_factor = 2
      # for every other component
      else:
          amplitude_factor = 2**(ncomp-1)

      # Calculate Limits
      if row['Model'] == 'Gaussian':
          max_i = [max_line, amplitude * amplitude_factor, maxsig]
          min_i = [min_line, 0.                          , minsig]

      elif row['Model'] == 'Lorentzian':
          max_i = [max_line, amplitude * amplitude_factor, 1.11*maxsig]
          min_i = [min_line, 0.                          , 1.11*minsig]

      elif row['Model'] == 'Voigt':
          max_i = [max_line, amplitude * amplitude_factor, maxsig, 1.11*maxsig]
          min_i = [min_line, 0.                          , minsig, 1.11*minsig]

      elif row['Model'] == 'Continuum':
          max_i = [np.inf, np.inf, np.inf]
          min_i = [-np.inf, 0    , -np.inf]
      else:
          print("Model not defined.")

      min_limits.append(min_i)
      max_limits.append(max_i)

  return min_limits, max_limits

def update_components(dfparams, additional_components_dict):
    '''
    This function takes a dataframe of a given emission line spectral model and
    updates it with additional components, in consistency with the lines specified
    in the additional_components_dict.

    dfparams: output of earlier mcmc runs
    additional_components_dict: a dictionary of the components to be added
    num: number of iterations to run the mcmc chains
    '''

    # Create a copy of the input dataframe to avoid modifying the original
    updated_df = dfparams.copy()

    # Remove error column
    updated_df = updated_df.drop(['Parameter Errors'], axis=1)

    # Iterate over the additional components dictionary

    for line, components in additional_components_dict.items():
      if (line in updated_df['Line Name'].values):
        print("Adding a {} component for {}".format(components[0], line))

        # Find the last instance of the element
        last_index = updated_df[updated_df['Line Name'] == line].index[-1]
        # Add a new component
        new_component_number = updated_df.loc[last_index, 'Component'] + 1

        # Copy the same parameters, updating the initial amplitude guess
        new_parameters = updated_df.loc[last_index, 'Parameters']
        new_parameters = [new_parameters[0], new_parameters[1]/2, new_parameters[2]]

        new_row = {'Line Name': line, 'Component': new_component_number, 'Model': components[0], 'Parameters': new_parameters}
        updated_df = pd.concat([updated_df[:last_index + 1], pd.DataFrame([new_row]), updated_df[last_index + 1:]], ignore_index=True)

      else:
        print(f"{line} not found in the Spectrum.")

    # Calculate limits

    min_limits, max_limits = minmaxlim(updated_df)

    updated_df['Max Limits'] = pd.Series(max_limits)
    updated_df['Min Limits'] = pd.Series(min_limits)

    # Return updateed dataframe

    return updated_df


# PLOTTING FUNCTION:   ==============================================================

def spl_plot(x, y, dy, dfparams, x_zoom=None, y_zoom=None, goodness_marks=None):
    # Extract data from the DataFrame

    x_fit = np.linspace(min(x) - 10, max(x) + 10, 10000)
    y_fit = np.zeros_like(x_fit)
    y_evaluated = np.zeros_like(x)

    # Create a figure with two subplots (upper and lower panels)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 5), sharex=True,
                                   gridspec_kw={'hspace': 0.0, 'height_ratios': [5, 2]})

    # Upper panel: Observed spectrum, model, and individual Gaussian components
    for _, row in dfparams.iterrows():
        line_name = row['Line Name']
        component = int(row['Component'])
        model = row['Model']

        if model == 'Continuum':
            component_y = spm.continuum_function(x_fit, *row['Parameters'])
            component_y_ev = spm.continuum_function(x, *row['Parameters'])
        if model == 'Gaussian':
            component_y = spm.gauss(x_fit, *row['Parameters'])
            component_y_ev = spm.gauss(x, *row['Parameters'])
        elif model == 'Lorentzian':
            component_y = spm.lorentzian(x_fit, *row['Parameters'])
            component_y_ev = spm.lorentzian(x, *row['Parameters'])
        elif model == 'Voigt':
            component_y = spm.voigt(x_fit, *row['Parameters'])
            component_y_ev = spm.voigt(x, *row['Parameters'])
        elif model == 'Asymmetric Gaussian':
            component_y = spm.asym_gauss(x_fit, *row['Parameters'])
            component_y_ev = spm.asym_gauss(x, *row['Parameters'])

        # Plot individually the component:

        color = (random.random(), random.random(), random.random())
        ax1.plot(x_fit, component_y, linestyle='--', linewidth=0.8, color=color)

        y_fit += component_y
        y_evaluated += component_y_ev

    ax1.plot(x_fit, y_fit, color='crimson', linewidth=2.0, label='Total Fitted Spectrum')
    ax1.errorbar(x, y, yerr=dy, color='grey', linestyle='-',
                 marker='.', alpha=0.7, markersize=2, linewidth=0.7, label='Observed Spectrum')

    # Set the y-axis label and legend for the upper panel
    ax1.set_ylabel(r'I [$10^{-17}$ erg s$^{-1}$ cm$^{-2}$ $\AA^{-1}$]')
    ax1.legend(loc='upper right', frameon=False)

    # Add Chi-squared and BIC as labels with transparency:
    if goodness_marks:
        chi2 = goodness_marks['reduced chi squared']
        BIC = goodness_marks['BIC']
        ax1.text(0.05, 0.85, f'Chi-squared: {chi2:.2f}', transform=ax1.transAxes, fontsize=12,
                 color='gray', alpha=0.8)
        ax1.text(0.05, 0.78, f'BIC: {BIC:.2f}', transform=ax1.transAxes, fontsize=12,
                 color='gray', alpha=0.8)

    residuals = (abs(y - y_evaluated) / y) * 100  # Compute percentage residuals
    e_range = [-0.5, 110]
    ax2.plot(x, residuals, marker='.', color='steelblue', linestyle='--', alpha=0.5,
             markersize=2, label='Percentage error')
    ax2.set_ylim(e_range)

    # Set the x-axis and y-axis labels for the lower panel
    ax2.set_xlabel(r'$\lambda$ [$\AA$]')
    ax2.set_ylabel('Residuals')
    ax2.legend(loc='upper right', frameon=False)

    # Set the zoom range if provided
    if x_zoom:
        ax1.set_xlim(x_zoom)
        ax2.set_xlim(x_zoom)
    if y_zoom:
        ax1.set_ylim(y_zoom)

    # Fine-tune the plot layout and remove top and right spines

    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)

    plt.tight_layout()
    plt.grid(False)
    

    return fig
def spl_savefile(fit, filename):
    # Extract the results

    theta_max = fit.fit_parameters
    theta_errors = fit.fit_errors
    models = fit.models
    continuum = fit.continuum
    goodness = fit.goodness
    lines = fit.model_parameters_df['Line Name'].unique()
    lines_list = list(lines)

    if 'Continuum' in lines_list:
        lines_list.remove('Continuum')


    # Create a dictionary to store the results
    results_dict = pd.DataFrame({
        'Line Name': [],
        'Model': [],
        'Component': [],
        'Centroid': [],
        'Amplitude': [],
        'Sigma / A factor': [],
        'Sigma (km/s)': [],
        'Gamma / w width': [],
        'Gamma (km/s)': [],
        'err_Centroid': [],
        'err_Amplitude': [],
        'err_Sigma / err_A': [],
        'err_Sigma (km/s)': [],
        'err_Gamma / err_w': [],
        'err_Gamma (km/s)': [],
    })

    # Populate the dictionary with the results
    param_start = 0
    r = 0 # Index that goes over the rows
    for line in lines_list:
        Ncomp = fit.model_parameters_df[fit.model_parameters_df['Line Name'] == line][
            'Component'].max()
        for j in range(Ncomp):
            results_dict.loc[r, 'Line Name'] = line
            results_dict.loc[r, 'Component'] = j+1
            model = models[r]
            results_dict.loc[r,'Model'] = model
            # Extracting parameters:s
            # Centroid:
            centroid = theta_max[param_start + j * 3]
            err_centroid = theta_errors[param_start + j * 3]
            # Amplitude:
            amplitude = theta_max[param_start + j * 3 + 1]
            err_amplitude = theta_errors[param_start + j * 3 + 1]

            # Sigma and Gamma:
            if models[r] == 'Gaussian':
                pn = 3
                sigma = theta_max[param_start + j * 3 + 2]
                err_sigma = theta_errors[param_start + j * 3 + 2]
                gamma = np.nan
                err_gamma = np.nan
            elif models[r] == 'Lorentzian':
                pn = 3
                gamma = theta_max[param_start + j * 3 + 2]
                err_gamma = theta_errors[param_start + j * 3 + 2]
                sigma = np.nan
                err_sigma = np.nan
            elif models[r] == 'Voigt':
                pn = 4
                sigma = theta_max[param_start + j * 3 + 2]
                gamma = theta_max[param_start + j * 3 + 3]
                err_sigma = theta_errors[param_start + j * 3 + 2]
                err_gamma = theta_errors[param_start + j * 3 + 3]
            elif models[r] == 'Asymmetric Gaussian':
                pn = 4
                sigma = theta_max[param_start + j * 3 + 2]
                gamma = theta_max[param_start + j * 3 + 3]
                err_sigma = theta_errors[param_start + j * 3 + 2]
                err_gamma = theta_errors[param_start + j * 3 + 3]


            sigma_kms = spm.vel_correct(sigma, centroid)
            gamma_kms = spm.vel_correct(gamma, centroid)
            err_sigma_kms = spm.vel_correct(err_sigma, centroid)
            err_gamma_kms = spm.vel_correct(err_gamma, centroid)

            # Appending results:
            results_dict.loc[r, 'Centroid'] = centroid
            results_dict.loc[r, 'Amplitude'] = amplitude
            results_dict.loc[r, 'Sigma / A factor'] = sigma
            results_dict.loc[r, 'Sigma (km/s)'] = sigma_kms
            results_dict.loc[r, 'Gamma / w width'] = gamma
            results_dict.loc[r, 'Gamma (km/s)'] = gamma_kms
            results_dict.loc[r, 'err_Centroid'] = err_centroid
            results_dict.loc[r, 'err_Amplitude'] = err_amplitude
            results_dict.loc[r, 'err_Sigma'] = err_sigma
            results_dict.loc[r, 'err_Sigma (km/s)'] = err_sigma_kms
            results_dict.loc[r, 'err_Gamma'] = err_gamma
            results_dict.loc[r, 'err_Gamma (km/s)'] = err_gamma_kms

            r += 1

        # Number of parameters per line
        params_per_line = pn * Ncomp
        param_start += params_per_line


    # Append continuum and goodness of fit
    results_dict.loc[r, 'Line Name'] = 'Continuum'
    results_dict.loc[r, 'Component'] = np.nan
    results_dict.loc[r, 'Model'] = 'Broken Power Law'
    results_dict.loc[r, 'Centroid'] = 'p1'
    results_dict.loc[r, 'Amplitude'] = 'p2'
    results_dict.loc[r, 'Sigma'] = 'p3'
    results_dict.loc[r+1, 'Line Name'] = 'Continuum'
    results_dict.loc[r+1, 'Component'] = np.nan
    results_dict.loc[r+1, 'Model'] = 'Broken Power Law'
    results_dict.loc[r+1, 'Centroid'] = continuum[0]
    results_dict.loc[r+1, 'Amplitude'] = continuum[1]
    results_dict.loc[r+1, 'Sigma'] = continuum[2]

    results_dict.loc[r+2, 'Line Name'] = 'Goodness'
    results_dict.loc[r+2, 'Component'] = np.nan
    results_dict.loc[r+2, 'Model'] = 'Goodness of Fit'
    results_dict.loc[r+2, 'Centroid'] = 'chi2'
    results_dict.loc[r+2, 'Amplitude'] = 'reduced chi2'
    results_dict.loc[r+2, 'Sigma'] = 'BIC'
    results_dict.loc[r+3, 'Line Name'] = 'Goodness'
    results_dict.loc[r+3, 'Component'] = np.nan
    results_dict.loc[r+3, 'Model'] = 'Goodness of Fit'
    results_dict.loc[r+3, 'Centroid'] = goodness['chi squared']
    results_dict.loc[r+3, 'Amplitude'] = goodness['reduced chi squared']
    results_dict.loc[r+3, 'Sigma'] = goodness['BIC']



    tab = pd.DataFrame(results_dict)
    tab.to_csv(filename, index=False)
