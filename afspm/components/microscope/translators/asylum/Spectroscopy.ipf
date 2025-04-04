#ifdef ARrtGlobals
#pragma rtGlobals=1     // Use modern global access method
#else
#pragma rtGlobals=3     // Use strict wave reference mode
#endif


// Initialize spots to 2 so we can set a probe pos
Function InitProbePos()
    WAVE spotX = root:Packages:MFP3D:Force:SpotX
    WAVE spotY = root:Packages:MFP3D:Force:SpotY

    // Redimension the spots so they only are of size 2
    // (1st is default and always should be middle of scan).
    Redimension /N=2 spotX
    Redimension /N=2 spotY
End Function

Function SetProbePosX(posX)
    Variable posX
    WAVE spotX = root:Packages:MFP3D:Force:SpotX
    spotX[1] = posX
End Function

Function GetProbePosX()
    WAVE spotX = root:Packages:MFP3D:Force:SpotX
    return spotX[1]  # TODO: Is this the way to return it?
End Function

Function GetProbePosY()
    WAVE spotY = root:Packages:MFP3D:Force:SpotY
    return spotY[1]
End Function

Function GetBaseName()
    WAVE baseName = root:
    return baseName
End Function

Function SetBaseName(newName)
    Variable newName  # TODO: Should this be a string? Is that a thing?
    WAVE baseName = root:
    baseName = newName
End Function
