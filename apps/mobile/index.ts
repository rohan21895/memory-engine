import "react-native-gesture-handler";

import { registerRootComponent } from 'expo';
import { AppRegistry } from 'react-native';

import App from './App';
import { SCAN_TASK_KEY, holdScanTask } from './modules/photeo-scan-service/src/index';

// Registered here, at the entry, for two reasons. The native service can start
// the task at any point after boot, so registration has to have already
// happened -- and doing it here keeps `react-native` out of the scan-service
// module's own imports, which the offline test runner cannot load.
//
// The task holds React Native's JS timer loop open while the app is off screen.
// Without it the scan stalls at its first batch boundary; see
// ScanForegroundService.
AppRegistry.registerHeadlessTask(SCAN_TASK_KEY, () => holdScanTask);

// registerRootComponent calls AppRegistry.registerComponent('main', () => App);
// It also ensures that whether you load the app in Expo Go or in a native build,
// the environment is set up appropriately
registerRootComponent(App);
